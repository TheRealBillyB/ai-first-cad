"""A document is the session an assistant works in.

Parts live in memory and are rebuilt from their source (a script, a list of
operations, a solved sketch, or a copied STEP file) when the workspace is
opened again. Geometry is millimeters.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import threading
from dataclasses import replace
from pathlib import Path

from aicad import kernel, library
from aicad.errors import DocumentError, GeometryError, PartNotFound
from aicad.models import (
    ClearanceReport,
    FeatureReport,
    InspectReport,
    Measurement,
    PartInfo,
    RenderedView,
    SectionReport,
    SketchReport,
    Vec3,
    WallReport,
)
from aicad.render import render_section, render_sketch, render_views
from aicad.sketch import Sketch, set_spec_dimension, solve_sketch

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_INTERFERENCE_VOLUME = 1e-4

_UNBUILDABLE = {"over_constrained", "didnt_converge", "too_many_unknowns"}

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
        base: kernel.Shape | None = None,
        placement: dict | None = None,
        library_spec: dict | None = None,
        sketch_name: str | None = None,
        sketch_steps: list[dict] | None = None,
    ):
        self.name = name
        self.source = source
        self.shape = shape
        self.base = base if base is not None else shape
        self.script = script
        self.ops = ops
        self.step_file = step_file
        self.placement = placement or _identity_placement()
        self.library_spec = library_spec
        self.sketch_name = sketch_name
        self.sketch_steps = sketch_steps


class _Sketch:
    def __init__(self, name: str, spec: dict):
        self.name = name
        self.spec = spec


class Document:
    """Named parts, rebuilt from source, with measurements and pictures."""

    def __init__(self, workspace: str | Path = "aicad_workspace"):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._parts: dict[str, _Part] = {}
        self._sketches: dict[str, _Sketch] = {}
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
            self._parts[name] = _Part(name, "step", shape, script=note, step_file=relative, base=shape)
            self._save()
            return self._info(self._parts[name])

    def update_part_script(self, name: str, script: str) -> PartInfo:
        """Replace a part's source. A failed script leaves the previous solid in place."""
        with self._lock:
            part = self._get(name)
            shape = kernel.make_from_script(script)
            part.shape = shape
            part.base = shape
            part.source = "script"
            part.script = script
            part.ops = None
            part.step_file = None
            part.library_spec = None
            part.sketch_name = None
            part.sketch_steps = None
            part.placement = _identity_placement()
            self._save()
            return self._info(part)

    def update_part_ops(self, name: str, ops: list[dict]) -> PartInfo:
        with self._lock:
            part = self._get(name)
            shape = kernel.make_from_ops(ops)
            part.shape = shape
            part.base = shape
            part.source = "ops"
            part.ops = list(ops)
            part.script = None
            part.step_file = None
            part.library_spec = None
            part.sketch_name = None
            part.sketch_steps = None
            part.placement = _identity_placement()
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
                "library": part.library_spec,
                "placement": part.placement,
                "sketch": part.sketch_name,
                "steps": part.sketch_steps,
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
            estimate = kernel.estimate_wall_thickness(self._get(name).shape, samples)
            passed = None
            if min_wall_mm is not None:
                passed = estimate.minimum_mm is not None and estimate.minimum_mm + 1e-9 >= min_wall_mm
            return WallReport(
                estimated_min_mm=estimate.minimum_mm,
                threshold_mm=min_wall_mm,
                passed=passed,
                sample_count=estimate.sample_count,
                samples_per_edge=estimate.samples_per_edge,
                thinnest_point_mm=estimate.thinnest_point_mm,
                method=estimate.method,
                note=estimate.note,
                ray_estimate_mm=estimate.ray_mm,
                section_estimate_mm=estimate.section_mm,
                section_count=estimate.section_count,
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
                features=part.shape.feature_report(),
                views=views,
                sketch=self._sketch_summary(part),
            )

    def feature_report(self, name: str) -> FeatureReport:
        with self._lock:
            return self._get(name).shape.feature_report()

    def section(self, name: str, plane: dict) -> SectionReport:
        """Cut the part with a plane and render the cross-section.

        ``plane`` is ``{"axis": "x"|"y"|"z", "offset": mm}`` or
        ``{"origin": [x, y, z], "normal": [x, y, z]}``.
        """
        with self._lock:
            part = self._get(name)
            origin, normal = _parse_plane(plane)
            report = kernel.section_geometry(part.shape, origin, normal)
            path = self.workspace / "renders" / name / f"{name}_section_{_plane_slug(plane)}.png"
            thickness = "n/a" if report.min_thickness_mm is None else f"{report.min_thickness_mm:g} mm"
            view = render_section(
                [loop.points_mm for loop in report.loops if loop.kind == "outer"],
                [loop.points_mm for loop in report.loops if loop.kind == "hole"],
                path,
                f"{name}  section",
                f"area {report.area_mm2:g} mm^2    section thickness {thickness}",
                _axis_label(report.plane_x_mm),
                _axis_label(report.plane_y_mm),
            )
            return replace(report, view=view)

    def create_sketch(self, name: str, spec: Sketch | dict) -> SketchReport:
        """Store a sketch spec and solve it. A conflict is reported, not rejected."""
        with self._lock:
            self._require_new_sketch(name)
            stored = _copy_spec(spec)
            report = self._solve_and_render(name, stored)
            self._sketches[name] = _Sketch(name, stored)
            self._save()
            return report

    def update_sketch(self, name: str, spec: Sketch | dict) -> SketchReport:
        """Replace a sketch. Dependent parts rebuild, or the previous spec stays."""
        with self._lock:
            sketch = self._get_sketch(name)
            stored = _copy_spec(spec)
            report = solve_sketch(stored, name)
            self._commit_sketch(sketch, stored, report)
            return self._solve_and_render(name, stored)

    def get_sketch(self, name: str) -> SketchReport:
        with self._lock:
            sketch = self._get_sketch(name)
            return self._solve_and_render(name, sketch.spec)

    def set_dimension(self, name: str, dimension: str, value: float) -> SketchReport:
        """Change one named driving dimension, re-solve, and rebuild dependent parts.

        A value that conflicts, or a rebuild that fails, leaves the previous
        dimension and the previous solids in place.
        """
        with self._lock:
            sketch = self._get_sketch(name)
            stored = set_spec_dimension(sketch.spec, dimension, value)
            report = solve_sketch(stored, name)
            if report.status in _UNBUILDABLE:
                raise GeometryError(report.note)
            self._commit_sketch(sketch, stored, report)
            return self._solve_and_render(name, stored)

    def create_part_from_sketch(self, name: str, sketch_name: str, steps: list[dict]) -> PartInfo:
        """Build a solid from a solved sketch. Over-constrained sketches are refused."""
        with self._lock:
            self._require_new_name(name)
            sketch = self._get_sketch(sketch_name)
            report = solve_sketch(sketch.spec, sketch.name)
            if report.status in _UNBUILDABLE:
                raise GeometryError(report.note)
            if not isinstance(steps, list) or not steps:
                raise GeometryError("steps must be a non-empty list of extrude, cut, revolve, cut_box, or gusset.")
            stored_steps = copy.deepcopy(steps)
            shape = kernel.solid_from_sketch(report.geometry, stored_steps)
            self._parts[name] = _Part(
                name,
                "sketch",
                shape,
                sketch_name=sketch_name,
                sketch_steps=stored_steps,
            )
            self._save()
            return self._info(self._parts[name])

    def update_part_from_sketch(self, name: str, sketch_name: str, steps: list[dict]) -> PartInfo:
        """Replace a part with a solid built from a sketch. A failed build keeps the old solid."""
        with self._lock:
            part = self._get(name)
            sketch = self._get_sketch(sketch_name)
            report = solve_sketch(sketch.spec, sketch.name)
            if report.status in _UNBUILDABLE:
                raise GeometryError(report.note)
            if not isinstance(steps, list) or not steps:
                raise GeometryError("steps must be a non-empty list of extrude, cut, revolve, cut_box, or gusset.")
            stored_steps = copy.deepcopy(steps)
            shape = kernel.solid_from_sketch(report.geometry, stored_steps)
            part.shape = shape
            part.base = shape
            part.source = "sketch"
            part.script = None
            part.ops = None
            part.step_file = None
            part.library_spec = None
            part.sketch_name = sketch_name
            part.sketch_steps = stored_steps
            part.placement = _identity_placement()
            self._save()
            return self._info(part)

    def list_sketches(self) -> list[str]:
        with self._lock:
            return list(self._sketches)

    def search_parts(self, query: str = "") -> dict[str, object]:
        return library.search(query)

    def insert_standard_part(
        self,
        name: str,
        kind: str,
        size: str,
        length: float | None = None,
        position: list | tuple | None = None,
        rotation_deg: list | tuple | None = None,
        fastener_type: str | None = None,
    ) -> PartInfo:
        """Insert a catalog fastener, nut, washer, or bearing, then place it."""
        with self._lock:
            self._require_new_name(name)
            placed_at = _vec3(position or [0, 0, 0], "position")
            rotation = _vec3(rotation_deg or [0, 0, 0], "rotation_deg")
            base = kernel.adopt(library.build(kind, size, length, fastener_type))
            shape = kernel.place(base, placed_at, rotation)
            spec = {
                "kind": kind,
                "size": size,
                "length": length,
                "fastener_type": fastener_type,
            }
            self._parts[name] = _Part(
                name,
                "library",
                shape,
                base=base,
                placement={"position": list(placed_at), "rotation_deg": list(rotation)},
                library_spec=spec,
            )
            self._save()
            return self._info(self._parts[name])

    def place_part(
        self,
        name: str,
        position: list | tuple,
        rotation_deg: list | tuple | None = None,
    ) -> PartInfo:
        """Move a part. The move replaces any previous placement; it does not stack."""
        with self._lock:
            part = self._get(name)
            placed_at = _vec3(position, "position")
            rotation = _vec3(rotation_deg or [0, 0, 0], "rotation_deg")
            part.placement = {"position": list(placed_at), "rotation_deg": list(rotation)}
            part.shape = kernel.place(part.base, placed_at, rotation)
            self._save()
            return self._info(part)

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

    def _commit_sketch(self, sketch: _Sketch, stored: dict, report: SketchReport) -> None:
        """Store a solved spec and rebuild parts that use it. Failure keeps the old spec."""
        dependents = [part for part in self._parts.values() if part.sketch_name == sketch.name]
        if report.status in _UNBUILDABLE:
            if dependents:
                raise GeometryError(report.note)
            sketch.spec = stored
            self._save()
            return
        rebuilt: list[tuple[_Part, kernel.Shape, kernel.Shape]] = []
        for part in dependents:
            base = kernel.solid_from_sketch(report.geometry, part.sketch_steps or [])
            placed = kernel.place(
                base,
                tuple(part.placement["position"]),
                tuple(part.placement["rotation_deg"]),
            )
            rebuilt.append((part, base, placed))
        sketch.spec = stored
        for part, base, placed in rebuilt:
            part.base = base
            part.shape = placed
        self._save()

    def _solve_and_render(self, name: str, spec: dict) -> SketchReport:
        report = solve_sketch(spec, name)
        path = self.workspace / "renders" / "sketches" / f"{name}.png"
        view = render_sketch(report, path)
        return replace(report, view=view)

    def _sketch_summary(self, part: _Part) -> dict[str, object] | None:
        if part.source != "sketch" or not part.sketch_name:
            return None
        sketch = self._sketches.get(part.sketch_name)
        if sketch is None:
            return {
                "name": part.sketch_name,
                "status": "missing",
                "note": f"Sketch {part.sketch_name!r} is not in this document.",
            }
        report = solve_sketch(sketch.spec, sketch.name)
        summary: dict[str, object] = {
            "name": sketch.name,
            "status": report.status,
            "dof": report.dof,
            "free": report.free,
            "conflicts": report.conflicts,
            "dimensions": report.dimensions,
            "note": report.note,
        }
        if report.status == "under_constrained":
            summary["warning"] = (
                "This solid was built from an under-constrained sketch. "
                "Free entities can still move. The shape is the current solution, not a locked one."
            )
        return summary

    def _get_sketch(self, name: str) -> _Sketch:
        _validate_name(name)
        sketch = self._sketches.get(name)
        if sketch is None:
            known = ", ".join(self._sketches) or "(none)"
            raise GeometryError(f"No sketch named {name!r}. Sketches in this document: {known}.")
        return sketch

    def _require_new_sketch(self, name: str) -> None:
        _validate_name(name)
        if name in self._sketches:
            raise GeometryError(
                f"Sketch {name!r} already exists. Update it, or use set_dimension to change a named dimension."
            )

    def _session_path(self) -> Path:
        return self.workspace / "session.json"

    def _save(self) -> None:
        payload = {
            "units": "mm",
            "sketches": [
                {"name": sketch.name, "spec": sketch.spec} for sketch in self._sketches.values()
            ],
            "parts": [
                {
                    "name": part.name,
                    "source": part.source,
                    "script": part.script,
                    "ops": part.ops,
                    "step_file": part.step_file,
                    "library": part.library_spec,
                    "placement": part.placement,
                    "sketch": part.sketch_name,
                    "steps": part.sketch_steps,
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
        for record in payload.get("sketches", []):
            self._sketches[record["name"]] = _Sketch(record["name"], record["spec"])
        for record in payload.get("parts", []):
            name = record["name"]
            source = record.get("source", "script")
            placement = record.get("placement") or _identity_placement()
            try:
                if source == "ops":
                    base = kernel.make_from_ops(record["ops"])
                elif source == "step":
                    base = kernel.import_step_file(self.workspace / record["step_file"])
                elif source == "library":
                    spec = record["library"]
                    base = kernel.adopt(
                        library.build(
                            spec["kind"],
                            spec["size"],
                            spec.get("length"),
                            spec.get("fastener_type"),
                        )
                    )
                elif source == "sketch":
                    sketch_name = record.get("sketch")
                    sketch = self._sketches.get(sketch_name)
                    if sketch is None:
                        raise DocumentError(
                            f"Part {name!r} needs sketch {sketch_name!r}, which is not in {path}."
                        )
                    solved = solve_sketch(sketch.spec, sketch.name)
                    if solved.status in _UNBUILDABLE:
                        raise DocumentError(f"Could not rebuild part {name!r}: {solved.note}")
                    base = kernel.solid_from_sketch(solved.geometry, record.get("steps") or [])
                else:
                    base = kernel.make_from_script(record.get("script") or "")
                shape = kernel.place(
                    base,
                    tuple(placement["position"]),
                    tuple(placement["rotation_deg"]),
                )
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
                base=base,
                placement=placement,
                library_spec=record.get("library"),
                sketch_name=record.get("sketch") if source == "sketch" else None,
                sketch_steps=record.get("steps") if source == "sketch" else None,
            )


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME.match(name):
        raise GeometryError(
            "Part names must start with a letter or underscore and contain only "
            "letters, numbers, underscores, and hyphens (64 characters max)."
        )


def _copy_spec(spec: Sketch | dict) -> dict:
    if isinstance(spec, Sketch):
        spec = spec.to_spec()
    if not isinstance(spec, dict):
        raise GeometryError("A sketch spec is an object with entities and constraints.")
    return copy.deepcopy(spec)


def _identity_placement() -> dict[str, list[float]]:
    return {"position": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]}


def _vec3(value, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError(f"{label} must be a list of three numbers.")
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a list of three numbers.") from exc


def _parse_plane(plane: dict) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if not isinstance(plane, dict):
        raise GeometryError(
            "plane must be {axis, offset} or {origin, normal}."
        )
    if "axis" in plane:
        axis = str(plane["axis"]).lower()
        if axis not in ("x", "y", "z"):
            raise GeometryError("plane axis must be 'x', 'y', or 'z'.")
        try:
            offset = float(plane.get("offset", 0.0))
        except (TypeError, ValueError) as exc:
            raise GeometryError("plane offset must be a number.") from exc
        index = {"x": 0, "y": 1, "z": 2}[axis]
        origin = [0.0, 0.0, 0.0]
        normal = [0.0, 0.0, 0.0]
        origin[index] = offset
        normal[index] = 1.0
        return (origin[0], origin[1], origin[2]), (normal[0], normal[1], normal[2])
    if "origin" not in plane or "normal" not in plane:
        raise GeometryError("plane needs axis and offset, or origin and normal.")
    return _vec3(plane["origin"], "plane origin"), _vec3(plane["normal"], "plane normal")


def _plane_slug(plane: dict) -> str:
    if isinstance(plane, dict) and "axis" in plane:
        offset = plane.get("offset", 0)
        text = f"{plane['axis']}_{offset:g}"
    else:
        text = "custom"
    return text.replace("-", "m").replace(".", "p")


def _axis_label(vector: Vec3) -> str:
    parts = []
    for name, value in (("X", vector.x), ("Y", vector.y), ("Z", vector.z)):
        if abs(value) >= 0.05:
            sign = "+" if value > 0 else "-"
            parts.append(f"{sign}{name}" if abs(abs(value) - 1) < 0.05 else f"{value:g} {name}")
    return " ".join(parts) if parts else "0"


def _validate_samples(samples: int) -> int:
    try:
        count = int(samples)
    except (TypeError, ValueError) as exc:
        raise GeometryError("samples must be an integer from 1 to 8.") from exc
    if count < 1 or count > 8:
        raise GeometryError("samples must be an integer from 1 to 8.")
    return count
