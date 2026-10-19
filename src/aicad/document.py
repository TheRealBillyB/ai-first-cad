"""A document is the session an assistant works in.

Parts live in memory and are rebuilt from their source (a script, a list of
operations, or a copied STEP file) when the workspace is opened again.
Geometry is millimeters.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

from aicad import kernel
from aicad.errors import DocumentError, GeometryError, PartNotFound
from aicad.models import (
    ClearanceReport,
    InspectReport,
    Measurement,
    PartInfo,
    RenderedView,
    WallReport,
)
from aicad.render import render_views

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_INTERFERENCE_VOLUME = 1e-4

_WALL_METHOD = "inward rays from a grid on each face"
_WALL_NOTE = (
    "Estimated by shooting rays inward from a grid on each face and measuring "
    "the distance to the next roughly parallel face. A thin spot between samples "
    "is not seen, so a passing result is evidence, not proof. Features thinner "
    "than about 0.15 mm are below the sample step and may be missed."
)
_CLEARANCE_NOTE = (
    "Interference is the volume of the boolean intersection, in mm^3. "
    "When the solids do not intersect, min_distance_mm is the gap between their "
    "surfaces. Penetration depth is not computed."
)


class _Part:
    def __init__(
        self,
        name: str,
        source: str,
        shape: kernel.Shape,
        script: str | None = None,
        ops: list[dict] | None = None,
        step_file: str | None = None,
    ):
        self.name = name
        self.source = source
        self.shape = shape
        self.script = script
        self.ops = ops
        self.step_file = step_file


class Document:
    """Named parts, rebuilt from source, with measurements and pictures."""

    def __init__(self, workspace: str | Path = "aicad_workspace"):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._parts: dict[str, _Part] = {}
        self._lock = threading.RLock()
        self._load()

    def create_part_from_script(self, name: str, script: str) -> PartInfo:
        with self._lock:
            self._require_new_name(name)
            shape = kernel.make_from_script(script)
            self._parts[name] = _Part(name, "script", shape, script=script)
            self._save()
            return self._info(self._parts[name])

    def create_part_from_ops(self, name: str, ops: list[dict]) -> PartInfo:
        with self._lock:
            self._require_new_name(name)
            shape = kernel.make_from_ops(ops)
            self._parts[name] = _Part(name, "ops", shape, ops=list(ops))
            self._save()
            return self._info(self._parts[name])

    def import_step(self, name: str, path: str | Path) -> PartInfo:
        with self._lock:
            self._require_new_name(name)
            source = Path(path).resolve()
            shape = kernel.import_step_file(source)
            relative = f"imports/{name}.step"
            destination = self.workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            note = f"# Imported STEP, copied to {relative}\n"
            self._parts[name] = _Part(name, "step", shape, script=note, step_file=relative)
            self._save()
            return self._info(self._parts[name])

    def update_part_script(self, name: str, script: str) -> PartInfo:
        """Replace a part's source. A failed script leaves the previous solid in place."""
        with self._lock:
            part = self._get(name)
            shape = kernel.make_from_script(script)
            part.shape = shape
            part.source = "script"
            part.script = script
            part.ops = None
            part.step_file = None
            self._save()
            return self._info(part)

    def update_part_ops(self, name: str, ops: list[dict]) -> PartInfo:
        with self._lock:
            part = self._get(name)
            shape = kernel.make_from_ops(ops)
            part.shape = shape
            part.source = "ops"
            part.ops = list(ops)
            part.script = None
            part.step_file = None
            self._save()
            return self._info(part)

    def delete_part(self, name: str) -> None:
        with self._lock:
            self._get(name)
            del self._parts[name]
            renders = self.workspace / "renders" / name
            if renders.exists():
                shutil.rmtree(renders)
            copied = self.workspace / "imports" / f"{name}.step"
            if copied.is_file():
                copied.unlink()
            self._save()

    def list_parts(self) -> list[PartInfo]:
        with self._lock:
            return [self._info(part) for part in self._parts.values()]

    def get_source(self, name: str) -> dict[str, object]:
        with self._lock:
            part = self._get(name)
            return {
                "name": part.name,
                "source": part.source,
                "script": part.script,
                "ops": part.ops,
                "step_file": part.step_file,
            }

    def measure(self, name: str) -> Measurement:
        with self._lock:
            return kernel.measure(self._get(name).shape)

    def check_wall_thickness(
        self,
        name: str,
        min_wall_mm: float | None = None,
        samples: int = 4,
    ) -> WallReport:
        with self._lock:
            samples = _validate_samples(samples)
            if min_wall_mm is not None and min_wall_mm < 0:
                raise GeometryError("min_wall_mm cannot be negative.")
            minimum, count, used, point = kernel.estimate_wall_thickness(
                self._get(name).shape, samples
            )
            passed = None
            if min_wall_mm is not None:
                passed = minimum is not None and minimum + 1e-9 >= min_wall_mm
            return WallReport(
                estimated_min_mm=minimum,
                threshold_mm=min_wall_mm,
                passed=passed,
                sample_count=count,
                samples_per_edge=used,
                thinnest_point_mm=point,
                method=_WALL_METHOD,
                note=_WALL_NOTE,
            )

    def check_clearance(
        self,
        part_a: str,
        part_b: str,
        min_clearance_mm: float | None = 0.0,
    ) -> ClearanceReport:
        with self._lock:
            if part_a == part_b:
                raise GeometryError("Clearance needs two different parts.")
            if min_clearance_mm is not None and min_clearance_mm < 0:
                raise GeometryError("min_clearance_mm cannot be negative.")
            first = self._get(part_a)
            second = self._get(part_b)
            overlap, distance, point_a, point_b = kernel.clearance_between(
                first.shape, second.shape
            )
            intersecting = overlap > _INTERFERENCE_VOLUME
            passed = None
            if min_clearance_mm is not None:
                passed = (not intersecting) and distance + 1e-9 >= min_clearance_mm
            return ClearanceReport(
                part_a=part_a,
                part_b=part_b,
                intersecting=intersecting,
                interference_volume_mm3=overlap,
                min_distance_mm=0.0 if intersecting else distance,
                closest_point_a_mm=None if intersecting else point_a,
                closest_point_b_mm=None if intersecting else point_b,
                threshold_mm=min_clearance_mm,
                passed=passed,
                note=_CLEARANCE_NOTE,
            )

    def render(self, name: str, wall: WallReport | None = None) -> list[RenderedView]:
        with self._lock:
            part = self._get(name)
            measurement = kernel.measure(part.shape)
            mesh = kernel.tessellate(part.shape)
            out_dir = self.workspace / "renders" / name
            return render_views(mesh, out_dir, name, measurement, wall)

    def export(self, name: str, path: str | Path, kind: str) -> Path:
        with self._lock:
            part = self._get(name)
            destination = Path(path)
            if not destination.is_absolute():
                destination = self.workspace / destination
            kind = kind.lower()
            if kind == "step":
                kernel.export_step_file(part.shape, destination)
            elif kind == "stl":
                kernel.export_stl_file(part.shape, destination)
            else:
                raise GeometryError(f"Unknown export format {kind!r}. Use 'step' or 'stl'.")
            return destination.resolve()

    def inspect(
        self,
        name: str,
        min_wall_mm: float | None = None,
        samples: int = 4,
    ) -> InspectReport:
        """Measure, estimate wall thickness, and render. This is the step to call after every edit."""
        with self._lock:
            part = self._get(name)
            measurement = kernel.measure(part.shape)
            wall = self.check_wall_thickness(name, min_wall_mm, samples)
            mesh = kernel.tessellate(part.shape)
            views = render_views(
                mesh,
                self.workspace / "renders" / name,
                name,
                measurement,
                wall,
            )
            return InspectReport(
                part=self._info(part),
                measurement=measurement,
                wall=wall,
                views=views,
            )

    def _info(self, part: _Part) -> PartInfo:
        measured = kernel.measure(part.shape)
        return PartInfo(
            name=part.name,
            source=part.source,
            units=measured.units,
            volume_mm3=measured.volume_mm3,
            valid=measured.valid,
            watertight=measured.watertight,
            bounding_box=measured.bounding_box,
        )

    def _get(self, name: str) -> _Part:
        _validate_name(name)
        part = self._parts.get(name)
        if part is None:
            known = ", ".join(self._parts) or "(none)"
            raise PartNotFound(f"No part named {name!r}. Parts in this document: {known}.")
        return part

    def _require_new_name(self, name: str) -> None:
        _validate_name(name)
        if name in self._parts:
            raise GeometryError(
                f"Part {name!r} already exists. Update it, or delete it before creating it again."
            )

    def _session_path(self) -> Path:
        return self.workspace / "session.json"

    def _save(self) -> None:
        payload = {
            "units": "mm",
            "parts": [
                {
                    "name": part.name,
                    "source": part.source,
                    "script": part.script,
                    "ops": part.ops,
                    "step_file": part.step_file,
                }
                for part in self._parts.values()
            ],
        }
        path = self._session_path()
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _load(self) -> None:
        path = self._session_path()
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DocumentError(f"Could not read {path}: {exc}") from exc
        for record in payload.get("parts", []):
            name = record["name"]
            source = record.get("source", "script")
            try:
                if source == "ops":
                    shape = kernel.make_from_ops(record["ops"])
                elif source == "step":
                    shape = kernel.import_step_file(self.workspace / record["step_file"])
                else:
                    shape = kernel.make_from_script(record.get("script") or "")
            except Exception as exc:
                raise DocumentError(
                    f"Could not rebuild part {name!r} from {path}: {exc}"
                ) from exc
            self._parts[name] = _Part(
                name,
                source,
                shape,
                script=record.get("script"),
                ops=record.get("ops"),
                step_file=record.get("step_file"),
            )


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME.match(name):
        raise GeometryError(
            "Part names must start with a letter or underscore and contain only "
            "letters, numbers, underscores, and hyphens (64 characters max)."
        )


def _validate_samples(samples: int) -> int:
    try:
        count = int(samples)
    except (TypeError, ValueError) as exc:
        raise GeometryError("samples must be an integer from 1 to 8.") from exc
    if count < 1 or count > 8:
        raise GeometryError("samples must be an integer from 1 to 8.")
    return count
