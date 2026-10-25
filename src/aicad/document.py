"""A document is the session an assistant works in.

Parts live in memory. Operations, sketches, catalog parts, and copied STEP
files are data, so opening the project rebuilds those. A part stored as a
Python script is not run on open: the solid is the STEP file written the last
time the script ran, and the script text is kept so it can be read. Geometry
is millimeters.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import threading
from dataclasses import replace
from pathlib import Path

from aicad import assembly, bom as bom_mod, gears as gears_mod
from aicad.assembly import posed_shapes as _posed_shapes
from aicad import drawing as drawing_mod
from aicad import explode as explode_mod
from aicad import fea, kernel, library
from aicad.bolts import check_joint
from aicad.modal import fatigue_check, solve_modal, solve_modal_assembly
from aicad.printprep import analyze_print
from aicad.printprep import list_printers as printer_catalog
from aicad.robot import export_robot
from aicad.errors import DocumentError, GeometryError, PartNotFound
from aicad.chain import solve_kinematics as solve_chain
from aicad.error_budget import check_position_error as budget_position_error
from aicad.kinematics import normalize as normalize_assembly
from aicad.kinematics import solve as solve_assembly
from aicad.motion_study import run_study, study_by_name
from aicad.power import actuators_for
from aicad.models import (
    BoundingBox,
    ClearanceReport,
    FeatureReport,
    FeaReport,
    InspectReport,
    Measurement,
    PackagingOverlap,
    PackagingPart,
    PackagingReport,
    PartInfo,
    RenderedView,
    SectionReport,
    SketchReport,
    Vec3,
    WallReport,
    feature_report_from_dict,
    round_mm,
)
from aicad.render import render_motion_frames, render_section, render_sketch, render_views
from aicad.sketch import Sketch, set_spec_dimension, solve_sketch

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_INTERFERENCE_VOLUME = 1e-4

_UNBUILDABLE = {"over_constrained", "didnt_converge", "too_many_unknowns"}

_CLEARANCE_NOTE = (
    "Interference is the volume of the boolean intersection, in mm^3. "
    "When the solids do not intersect, min_distance_mm is the gap between their "
    "surfaces. Penetration depth is not computed."
)

_PACKAGING_NOTE = (
    "The envelope solid is the allowed volume. min_clearance_mm is the smallest "
    "signed distance from a part to the envelope wall: positive is a gap inside "
    "the wall, zero touches it, and negative means the part sticks out by that "
    "many millimeters. protrusion_at_mm is a point on the part where the "
    "protrusion is reached. Overlap is the volume of two parts that also lies "
    "inside the envelope. A part passes when its clearance meets the required "
    "gap. The check passes when every part passes and no overlap is reported."
)


class _Part:
    def __init__(
        self,
        name: str,
        source: str,
        shape: kernel.Shape | None,
        script: str | None = None,
        ops: list[dict] | None = None,
        step_file: str | None = None,
        base: kernel.Shape | None = None,
        placement: dict | None = None,
        library_spec: dict | None = None,
        sketch_name: str | None = None,
        sketch_steps: list[dict] | None = None,
        geometry_file: str | None = None,
        script_sha256: str | None = None,
        script_pending: bool = False,
        rebuild_error: str | None = None,
        gear_spec: dict | None = None,
    ):
        self.name = name
        self.source = source
        self._shape = shape
        self._base = base if base is not None else shape
        self.script = script
        self.ops = ops
        self.step_file = step_file
        self.placement = placement or _identity_placement()
        self.library_spec = library_spec
        self.sketch_name = sketch_name
        self.sketch_steps = sketch_steps
        self.geometry_file = geometry_file
        self.script_sha256 = script_sha256
        self.script_pending = script_pending
        self.rebuild_error = rebuild_error
        self.gear_spec = gear_spec

    @property
    def shape(self) -> kernel.Shape:
        if self._shape is None:
            if self.rebuild_error:
                raise DocumentError(self.rebuild_error)
            raise DocumentError(_unbuilt_message(self.name))
        return self._shape

    @shape.setter
    def shape(self, value: kernel.Shape | None) -> None:
        self._shape = value

    @property
    def base(self) -> kernel.Shape:
        if self._base is None:
            raise DocumentError(_unbuilt_message(self.name))
        return self._base

    @base.setter
    def base(self, value: kernel.Shape | None) -> None:
        self._base = value


class _Sketch:
    def __init__(self, name: str, spec: dict):
        self.name = name
        self.spec = spec


class Document:
    """Named parts, rebuilt from source, with measurements and pictures."""

    def __init__(self, workspace: str | Path = "aicad_workspace"):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        from aicad.paths import remember_project

        remember_project(self.workspace)
        self._parts: dict[str, _Part] = {}
        self._sketches: dict[str, _Sketch] = {}
        self._assemblies: dict[str, dict] = {}
        self.problems: list[str] = []
        self._lock = threading.RLock()
        self.actor = "user"
        self._load()

    def create_part_from_script(self, name: str, script: str) -> PartInfo:
        with self._lock:
            self._require_new_name(name)
            shape = kernel.make_from_script(script, workspace=self.workspace)
            relative = self._store_script_geometry(name, script, shape)
            self._parts[name] = _Part(
                name,
                "script",
                shape,
                script=script,
                geometry_file=relative,
                script_sha256=_script_hash(script),
            )
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
            from aicad.paths import publish_bytes, refuse_reserved_device
            from aicad.sandbox import require_in_project

            require_in_project(path, self.workspace, writing=False)
            source = _step_import_source(self.workspace, path)
            shape = kernel.import_step_file(source)
            relative = f"imports/{name}.step"
            refuse_reserved_device(relative, None, "Import copy", GeometryError)
            destination = _project_file(self.workspace, self.workspace / relative, "Imported STEP")
            publish_bytes(destination, source.read_bytes())
            note = f"# Imported STEP, copied to {relative}\n"
            self._parts[name] = _Part(name, "step", shape, script=note, step_file=relative, base=shape)
            self._save()
            return self._info(self._parts[name])

    def update_part_script(self, name: str, script: str) -> PartInfo:
        """Replace a part's source. A failed script leaves the previous solid in place."""
        with self._lock:
            part = self._get(name)
            shape = kernel.make_from_script(script, workspace=self.workspace)
            part.shape = shape
            part.base = shape
            part.source = "script"
            part.script = script
            part.ops = None
            part.step_file = None
            part.library_spec = None
            part.gear_spec = None
            part.sketch_name = None
            part.sketch_steps = None
            part.placement = _identity_placement()
            part.geometry_file = self._store_script_geometry(name, script, shape)
            part.script_sha256 = _script_hash(script)
            part.script_pending = False
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
            part.gear_spec = None
            part.sketch_name = None
            part.sketch_steps = None
            part.placement = _identity_placement()
            part.geometry_file = None
            part.script_sha256 = None
            part.script_pending = False
            self._save()
            return self._info(part)

    def delete_part(self, name: str) -> None:
        with self._lock:
            self._get(name)
            renders = self.workspace / "renders" / name
            remove_link = False
            if renders.exists() or renders.is_symlink():
                from aicad.paths import is_reparse

                remove_link = is_reparse(renders)
                _project_resolved(self.workspace, renders, "Render")
            del self._parts[name]
            if renders.exists() or renders.is_symlink():
                if remove_link:
                    renders.unlink()
                else:
                    shutil.rmtree(renders)
            # The copied STEP file stays so undo can rebuild an imported part.
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
                "gear": copy.deepcopy(part.gear_spec) if part.gear_spec else None,
                "gear_data": gears_mod.gear_data(part.gear_spec) if part.gear_spec else None,
                "placement": part.placement,
                "sketch": part.sketch_name,
                "steps": part.sketch_steps,
                "built": part._shape is not None,
                "script_pending": part.script_pending,
                "geometry_file": part.geometry_file,
                "problem": part.rebuild_error,
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
                # A capped reading is an upper bound. It must not pass a minimum.
                passed = (
                    estimate.minimum_mm is not None
                    and not estimate.capped
                    and estimate.minimum_mm + 1e-9 >= min_wall_mm
                )
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

    def check_packaging(
        self,
        envelope: str | dict,
        parts: list[str] | None = None,
        assembly: str | None = None,
        pose: str | None = None,
        joints: dict | None = None,
        min_clearance_mm: float = 0.0,
    ) -> PackagingReport:
        """Check that parts fit inside an envelope.

        ``envelope`` is a part name, or an object: a primitive
        (``shape`` box, cylinder, or sphere) or an ``outline`` extruded
        along an axis. With ``assembly``, the parts are the posed instances.
        ``pose`` and ``joints`` select that pose for this check and do not
        change the stored assembly. A joint value is a number: degrees on a
        revolute or cylindrical joint, millimetres on a prismatic joint. A
        cylindrical joint also accepts ``{"value": degrees, "slide": millimetres}``.
        ``min_clearance_mm`` is the required gap
        to the envelope wall. Zero allows a part to touch the wall.
        """
        with self._lock:
            required = kernel.finite_number(min_clearance_mm, "min_clearance_mm")
            if required < 0:
                raise GeometryError("min_clearance_mm cannot be negative.")
            posed = None
            solved = None
            assembly_name = None
            if assembly is not None:
                if not isinstance(assembly, str) or not assembly.strip():
                    raise GeometryError("assembly must be a name.")
                assembly_name = assembly.strip()
                spec = self._get_assembly(assembly_name)
                overrides = _packaging_overrides(spec, pose, joints)
                solved, posed = _posed_shapes(spec, self._assembly_shapes(spec), overrides or None)
            envelope_shape, envelope_info, envelope_instance = self._packaging_envelope(envelope, posed, solved)
            names = self._packaging_names(parts, posed, solved, envelope_instance)
            if envelope_info.get("kind") == "part" and envelope_info.get("name") in names:
                raise GeometryError(
                    f"{envelope_info['name']!r} is the envelope, so it is not one of the parts being packed."
                )
            shapes = self._packaging_shapes(names, posed)
            rows: list[PackagingPart] = []
            for name in names:
                fit = kernel.fit_in_envelope(shapes[name], envelope_shape)
                clearance = float(fit["min_clearance_mm"])
                protrusion = float(fit["max_protrusion_mm"])
                protrudes = not bool(fit["contained"])
                point = _pack_vec(fit["clearance_at_mm"])
                poke = None if fit["protrusion_at_mm"] is None or not protrudes else _pack_vec(fit["protrusion_at_mm"])
                rows.append(
                    PackagingPart(
                        name=name,
                        passed=clearance + 1e-9 >= required,
                        contained=bool(fit["contained"]) and not protrudes,
                        protrudes=protrudes,
                        min_clearance_mm=round_mm(clearance),
                        clearance_at_mm=point,
                        max_protrusion_mm=0.0 if not protrudes else round_mm(protrusion),
                        protrusion_at_mm=poke,
                        outside_volume_mm3=0.0 if not protrudes else round_mm(float(fit["outside_volume_mm3"])),
                        method=str(fit["method"]),
                    )
                )
            overlaps: list[PackagingOverlap] = []
            ordered = list(names)
            for index, left in enumerate(ordered):
                for right in ordered[index + 1 :]:
                    hit = kernel.overlap_inside(shapes[left], shapes[right], envelope_shape)
                    if hit is None:
                        continue
                    first, second = sorted((left, right))
                    overlaps.append(
                        PackagingOverlap(
                            part_a=first,
                            part_b=second,
                            volume_mm3=round_mm(float(hit["volume_mm3"])),
                            at_mm=_pack_vec(hit["at"]),
                        )
                    )
            overlaps.sort(key=lambda item: (item.part_a, item.part_b))
            worst = min(rows, key=lambda item: (item.min_clearance_mm, item.name))
            protruding = tuple(item.name for item in rows if item.protrudes)
            joint_values = None
            if solved is not None:
                joint_values = {}
                for joint in solved["joints"]:
                    if joint["type"] == "cylindrical":
                        joint_values[joint["name"]] = {
                            "value": joint["value"],
                            "slide": joint["slide"],
                        }
                    else:
                        joint_values[joint["name"]] = joint["value"]
            passed = all(item.passed for item in rows) and not overlaps
            return PackagingReport(
                passed=passed,
                required_clearance_mm=round_mm(required),
                min_clearance_mm=worst.min_clearance_mm,
                min_clearance_part=worst.name,
                min_clearance_at_mm=worst.clearance_at_mm,
                envelope=envelope_info,
                parts=tuple(rows),
                protruding=protruding,
                overlaps=tuple(overlaps),
                assembly=assembly_name,
                pose=pose,
                joints=joint_values,
                note=_PACKAGING_NOTE,
            )

    def _packaging_envelope(self, envelope, posed: dict | None, solved: dict | None):
        if isinstance(envelope, dict):
            shape = kernel.make_envelope(envelope)
            if "outline" in envelope:
                info = {
                    "kind": "outline",
                    "name": None,
                    "axis": list(envelope.get("axis", [0, 0, 1])),
                }
            else:
                info = {
                    "kind": "primitive",
                    "name": None,
                    "shape": envelope.get("shape", envelope.get("primitive")),
                }
            return shape, info, None
        if not isinstance(envelope, str) or not envelope.strip():
            raise GeometryError("Name an envelope part, or pass a primitive or an outline.")
        name = envelope.strip()
        if posed is not None and name in posed:
            part_name = next(instance["part"] for instance in solved["instances"] if instance["name"] == name)
            return posed[name], {"kind": "instance", "name": name, "part": part_name}, name
        if name in self._parts:
            return self._get(name).shape, {"kind": "part", "name": name}, None
        if posed is not None:
            known = ", ".join(posed) or "(none)"
            raise PartNotFound(
                f"No envelope named {name!r}. Instances: {known}."
            )
        known = ", ".join(self._parts) or "(none)"
        raise PartNotFound(f"No part named {name!r}. Parts in this document: {known}.")

    def _packaging_names(self, parts, posed: dict | None, solved: dict | None, envelope_instance: str | None) -> list[str]:
        if parts is None:
            if posed is None:
                raise GeometryError("Name the parts to pack, or pass an assembly.")
            names = [
                instance["name"]
                for instance in solved["instances"]
                if instance["name"] != envelope_instance
            ]
        else:
            if not isinstance(parts, (list, tuple)):
                raise GeometryError("parts must be a list of names.")
            names = []
            for item in parts:
                if not isinstance(item, str) or not item.strip():
                    raise GeometryError("Each part name must be a non-empty string.")
                names.append(item.strip())
        if len(names) != len(set(names)):
            raise GeometryError("A part is listed twice.")
        if envelope_instance is not None and envelope_instance in names:
            raise GeometryError(
                f"{envelope_instance!r} is the envelope, so it is not one of the parts being packed."
            )
        if not names:
            raise GeometryError("There is no part to pack. The envelope is not checked against itself.")
        return names

    def _packaging_shapes(self, names: list[str], posed: dict | None) -> dict[str, kernel.Shape]:
        shapes: dict[str, kernel.Shape] = {}
        if posed is not None:
            known = ", ".join(posed) or "(none)"
            for name in names:
                if name not in posed:
                    raise GeometryError(f"No instance {name!r} in this assembly. Instances: {known}.")
                shapes[name] = posed[name]
            return shapes
        for name in names:
            shapes[name] = self._get(name).shape
        return shapes

    def render(self, name: str, wall: WallReport | None = None) -> list[RenderedView]:
        with self._lock:
            part = self._get(name)
            measurement = kernel.measure(part.shape)
            mesh = kernel.tessellate(part.shape)
            out_dir = _project_directory(self.workspace, self.workspace / "renders" / name, "Render")
            return render_views(mesh, out_dir, name, measurement, wall)

    def export(self, name: str, path: str | Path, kind: str) -> Path:
        with self._lock:
            from aicad.sandbox import require_in_project

            require_in_project(path, self.workspace, writing=True)
            part = self._get(name)
            from aicad.paths import refuse_reserved_device, require_export_path

            refuse_reserved_device(path, self.workspace, "Export path", GeometryError)
            destination = Path(path)
            if not destination.is_absolute():
                # A relative export stays in the project. Windows trims a
                # trailing dot or space before creating the file, and that
                # trimmed path is refused when it would leave the project.
                destination = require_export_path(self.workspace, path, "Export path", GeometryError)
            destination = _project_file(
                self.workspace, destination, "Export path", allow_absolute_outside=True
            )
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
                _project_directory(self.workspace, self.workspace / "renders" / name, "Render"),
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
            path = _project_file(
                self.workspace,
                self.workspace / "renders" / name / f"{name}_section_{_plane_slug(plane)}.png",
                "Section",
            )
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
            kernel.finite_number(value, "dimension")
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
            part.gear_spec = None
            part.sketch_name = sketch_name
            part.sketch_steps = stored_steps
            part.placement = _identity_placement()
            self._save()
            return self._info(part)

    def list_sketches(self) -> list[str]:
        with self._lock:
            return list(self._sketches)

    def list_materials(self) -> dict[str, object]:
        """Handbook materials the linear solver can use."""
        return fea.list_materials()

    def describe_faces(self, name: str) -> dict[str, object]:
        """Faces an assistant can name in a support or a load.

        Booleans do not keep feature names on faces, so a selector is geometric:
        a plane normal, a cylinder radius, a location, or a face index.
        """
        with self._lock:
            shape = self._get(name).shape
            faces = kernel.describe_faces(shape)
        return {
            "part": name,
            "units": "mm",
            "faces": faces,
            "note": (
                "Pick a face by kind. A plane uses its outward normal, and at chooses "
                "the closest match. A cylinder uses radius (default tolerance 0.05 mm) "
                "and an optional axis; omit at to take every match, such as four frame holes. "
                "nearest uses at and returns the face whose center is closest."
            ),
        }

    def solve_mechanics(self, name: str, setup: dict) -> FeaReport:
        """Mesh the part and run a linear static solve. Pictures land next to the other renders."""
        with self._lock:
            part = self._get(name)
            out_dir = _project_directory(self.workspace, self.workspace / "renders" / name, "Render")
            return fea.solve_part(part.shape, setup, out_dir=out_dir, stem="fea", part_name=name)

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
        """Insert a catalog part by designation, then place it."""
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

    def create_gear(
        self,
        name: str,
        spec: dict,
        position: list | tuple | None = None,
        rotation_deg: list | tuple | None = None,
    ) -> dict:
        """Build an involute gear. The spec is stored and the solid is rebuilt from it."""
        with self._lock:
            self._require_new_name(name)
            return self._write_gear(name, spec, position, rotation_deg)

    def update_gear(
        self,
        name: str,
        spec: dict,
        position: list | tuple | None = None,
        rotation_deg: list | tuple | None = None,
    ) -> dict:
        """Replace a part with a gear. A failed build leaves the previous solid."""
        with self._lock:
            self._get(name)
            return self._write_gear(name, spec, position, rotation_deg)

    def _write_gear(self, name: str, spec: dict, position, rotation_deg) -> dict:
        from aicad.honesty import VERIFIED_CHECK, finish

        clean = gears_mod.normalize_spec(spec)
        base = kernel.adopt(gears_mod.build_gear(clean))
        previous = self._parts.get(name)
        if position is None and previous is not None:
            position = previous.placement["position"]
        if rotation_deg is None and previous is not None:
            rotation_deg = previous.placement["rotation_deg"]
        placed_at = _vec3(position or [0, 0, 0], "position")
        rotation = _vec3(rotation_deg or [0, 0, 0], "rotation_deg")
        shape = kernel.place(base, placed_at, rotation)
        self._parts[name] = _Part(
            name,
            "gear",
            shape,
            base=base,
            placement={"position": list(placed_at), "rotation_deg": list(rotation)},
            gear_spec=clean,
        )
        self._save()
        payload = self._info(self._parts[name]).to_dict()
        payload["gear"] = gears_mod.gear_data(clean)
        if clean["type"] == "hypoid":
            what = (
                "Hypoid ring or pinion. The ring tooth is a face-mill blade cone. "
                "The pinion is the blank left after that tooth is rolled at the tooth ratio."
            )
            limits = [
                "This is not a tolerance grade or a load rating.",
                "The cutter is one face-mill blade cone. Face-hobbing is not generated.",
                "The edge radius is recorded and is not cut as a fillet.",
                "The flanks are shifted slightly apart so a flat facet cannot cross the mate.",
            ]
        else:
            what = (
                "Nominal involute gear from the cited tooth standard. "
                "The solid and the pitch, base, and cone figures describe that model."
            )
            limits = [
                "Tooth thickness is the nominal zero-backlash size, not a tolerance grade.",
                "A helical fillet uses the ISO 53 normal tip radius as a circle in the transverse plane.",
                "A bevel solid rules the back-cone involute between the inner and outer cone distances. Reported face and root angles are the Gleason parallel-clearance angles.",
                "A spiral trace is a constant-angle spiral, not a circular cutter path.",
            ]
        return finish(
            payload,
            kind=VERIFIED_CHECK,
            name=name,
            what=what,
            limits=limits,
        )

    def place_part(
        self,
        name: str,
        position: list | tuple,
        rotation_deg: list | tuple | None = None,
    ) -> PartInfo:
        """Move a part. The move replaces any previous placement; it does not stack.

        A bad position is rejected before the part changes, so a later save
        cannot write the failed placement into the session.
        """
        with self._lock:
            part = self._get(name)
            placed_at = _vec3(position, "position")
            rotation = _vec3(rotation_deg or [0, 0, 0], "rotation_deg")
            shape = kernel.place(part.base, placed_at, rotation)
            part.placement = {"position": list(placed_at), "rotation_deg": list(rotation)}
            part.shape = shape
            self._save()
            return self._info(part)

    def _info(self, part: _Part) -> PartInfo:
        if part._shape is None:
            zero = Vec3(0.0, 0.0, 0.0)
            return PartInfo(
                name=part.name,
                source=part.source,
                units=kernel.UNITS,
                volume_mm3=0.0,
                valid=False,
                watertight=False,
                bounding_box=BoundingBox(zero, zero, zero),
                built=False,
            )
        measured = kernel.measure(part.shape)
        return PartInfo(
            name=part.name,
            source=part.source,
            units=measured.units,
            volume_mm3=measured.volume_mm3,
            valid=measured.valid,
            watertight=measured.watertight,
            bounding_box=measured.bounding_box,
            built=True,
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
        path = _project_file(self.workspace, self.workspace / "renders" / "sketches" / f"{name}.png", "Sketch")
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

    def create_assembly(self, spec: dict) -> dict:
        """Store an assembly of parts that already exist in this document."""
        with self._lock:
            name = _assembly_name(spec)
            if name in self._assemblies:
                raise GeometryError(
                    f"Assembly {name!r} already exists. Update it, or choose another name."
                )
            return self._commit_assembly(spec)

    def update_assembly(self, spec: dict) -> dict:
        with self._lock:
            name = _assembly_name(spec)
            if name not in self._assemblies:
                known = ", ".join(self._assemblies) or "(none)"
                raise GeometryError(f"No assembly named {name!r}. Assemblies: {known}.")
            return self._commit_assembly(spec)

    def get_assembly(self, name: str) -> dict:
        with self._lock:
            spec = self._get_assembly(name)
            return {"assembly": copy.deepcopy(spec), "solved": solve_assembly(spec)}

    def list_assemblies(self) -> list[dict]:
        """Names, joint values, poses, motions, and studies. This does not place solids."""
        with self._lock:
            listed = []
            for spec in self._assemblies.values():
                listed.append(
                    {
                        "name": spec["name"],
                        "instances": [
                            {"name": instance["name"], "part": instance["part"]}
                            for instance in spec["instances"]
                        ],
                        "joints": [
                            {
                                "name": joint["name"],
                                "type": joint["type"],
                                "parent": joint["parent"],
                                "child": joint["child"],
                                "value": joint["value"],
                                "slide": joint["slide"],
                                "min": joint.get("min"),
                                "max": joint.get("max"),
                            }
                            for joint in spec["joints"]
                        ],
                        "poses": sorted(spec["poses"]),
                        "motions": sorted(spec["motions"]),
                        "studies": sorted(spec.get("studies") or {}),
                        "cables": [cable.get("name", f"cable_{index + 1}") for index, cable in enumerate(spec["cables"])],
                        "actuators": [
                            actuator.get("name") or actuator.get("joint") for actuator in spec["actuators"]
                        ],
                    }
                )
            return listed

    def set_joint(self, name: str, joint: str, value: float, slide: float | None = None) -> dict:
        with self._lock:
            spec = copy.deepcopy(self._get_assembly(name))
            found = next((item for item in spec["joints"] if item["name"] == joint), None)
            if found is None:
                known = ", ".join(item["name"] for item in spec["joints"]) or "(none)"
                raise GeometryError(f"No joint {joint!r} on {name!r}. Joints: {known}.")
            angular = found["type"] in ("revolute", "cylindrical")
            found["value"] = kernel.finite_number(
                value,
                "joint value",
                unit="deg" if angular else "mm",
                bound=None if angular else kernel.MAX_MM,
            )
            if slide is not None:
                found["slide"] = kernel.finite_number(slide, "joint slide")
            result = self._commit_assembly(spec)
            driven = next((row for row in result["solved"]["joints"] if row["name"] == joint), None)
            if driven and driven["driven"]:
                result["note"] = (
                    f"{joint} is driven by a relation, so its value follows the driver."
                )
            return result

    def set_pose(self, name: str, pose: str) -> dict:
        with self._lock:
            spec = copy.deepcopy(self._get_assembly(name))
            if pose not in spec["poses"]:
                known = ", ".join(spec["poses"]) or "(none)"
                raise GeometryError(f"No pose {pose!r} on {name!r}. Poses: {known}.")
            for joint in spec["joints"]:
                if joint["name"] in spec["poses"][pose]:
                    joint["value"] = float(spec["poses"][pose][joint["name"]])
            result = self._commit_assembly(spec)
            result["pose"] = pose
            return result

    def run_motion(
        self,
        name: str,
        sequence: str | None = None,
        motion: dict | None = None,
        render: bool = True,
    ) -> dict:
        with self._lock:
            spec = self._get_assembly(name)
            chosen = assembly.motion_by_name(spec, sequence, motion)
            if chosen is None:
                known = ", ".join(spec["motions"]) or "(none)"
                raise GeometryError(f"Name a motion on {name!r}, or pass one. Motions: {known}.")
            out = None
            if render:
                out = _project_directory(
                    self.workspace, self.workspace / "renders" / "assemblies" / spec["name"], "Motion"
                )
            return assembly.run_motion(spec, self._assembly_shapes(spec), chosen, out)

    def run_motion_study(
        self,
        name: str,
        sequence: str | None = None,
        study: dict | None = None,
        steps: int | None = None,
        clearance_mm: float | None = None,
    ) -> dict:
        """Drive the assembly through a motion study and report collisions and limit hits."""
        with self._lock:
            spec = self._get_assembly(name)
            chosen = study_by_name(spec, sequence, study)
            if chosen is None:
                known = ", ".join(spec.get("studies") or {}) or "(none)"
                raise GeometryError(f"Name a motion study on {name!r}, or pass one. Studies: {known}.")
            return run_study(
                spec,
                self._assembly_shapes(spec),
                chosen,
                steps=steps,
                clearance_mm=clearance_mm,
            )

    def solve_kinematics(
        self,
        name: str,
        tool: str,
        at: list | None = None,
        rotation_deg: list | None = None,
        joints: dict | None = None,
        slides: dict | None = None,
        target: dict | None = None,
        path: dict | None = None,
    ) -> dict:
        """Forward or inverse kinematics for the chain that ends at ``tool``.

        ``target`` is a position, an orientation, or a rigid matrix. ``path``
        steps the tool along a line or through waypoints and checks those
        poses with the motion study.
        """
        request: dict = {"tool": tool}
        for key, value in (
            ("at", at),
            ("rotation_deg", rotation_deg),
            ("joints", joints),
            ("slides", slides),
            ("target", target),
            ("path", path),
        ):
            if value is not None:
                request[key] = value
        with self._lock:
            spec = self._get_assembly(name)
            return solve_chain(spec, self._assembly_shapes(spec), request)

    def check_position_error(self, name: str, request: dict) -> dict:
        """Tip position error of the chain that ends at ``request['tool']``.

        Joint sources are an encoder step, a backlash, and a stiffness under a
        stated load. A mesh backlash is a gear spec or a gear part name. A link
        source is a stated screw or a strength-check setup. The pass is the
        worst case against ``tolerance_mm``.
        """
        if not isinstance(request, dict):
            raise GeometryError("A position error request is an object.")
        with self._lock:
            spec = self._get_assembly(name)
            resolved = self._resolve_position_sources(request)
            out_dir = None
            if _position_request_has_fea(resolved):
                out_dir = _project_directory(
                    self.workspace,
                    self.workspace / "renders" / "assemblies" / spec["name"] / "position-error",
                    "Render",
                )
            return budget_position_error(spec, self._assembly_shapes(spec), resolved, out_dir=out_dir)

    def _resolve_position_sources(self, request: dict) -> dict:
        request = copy.deepcopy(request)
        sources = request.get("sources")
        if not isinstance(sources, dict):
            return request
        for source in sources.values():
            if not isinstance(source, dict):
                continue
            blocks = []
            backlash = source.get("backlash")
            if isinstance(backlash, dict) and isinstance(backlash.get("mesh"), dict):
                blocks.append(backlash["mesh"])
            for channel in ("angle", "slide"):
                inner = source.get(channel)
                if not isinstance(inner, dict):
                    continue
                nested = inner.get("backlash")
                if isinstance(nested, dict) and isinstance(nested.get("mesh"), dict):
                    blocks.append(nested["mesh"])
            for mesh in blocks:
                if isinstance(mesh.get("pinion"), str) or isinstance(mesh.get("gear"), str):
                    mesh.update(self._resolve_mesh_block(mesh))
        return request

    def _resolve_mesh_block(self, mesh: dict) -> dict:
        cleaned = dict(mesh)
        pinion_spec, pinion_part = self._gear_member(mesh.get("pinion"), "pinion")
        gear_spec, gear_part = self._gear_member(mesh.get("gear"), "gear")
        cleaned["pinion"] = pinion_spec
        cleaned["gear"] = gear_spec
        if pinion_part is not None and gear_part is not None:
            mounting = self._gear_mounting(cleaned, pinion_part, gear_part)
            for key in ("centre_distance_mm", "shaft_angle_deg", "offset_mm", "pinion_phase_deg", "gear_phase_deg"):
                if mounting.get(key) is not None and cleaned.get(key) is None:
                    cleaned[key] = mounting[key]
        return cleaned

    def check_cables(
        self,
        name: str,
        sequence: str | None = None,
        motion: dict | None = None,
        study: dict | str | None = None,
        steps: int | None = None,
    ) -> dict:
        """Length, bend, and pinch for clip routes and tendon routes.

        ``sequence`` is a motion, or a motion study when no motion has that name.
        ``study`` is a study name or an inline study. ``steps`` overrides the
        study's pose count. A tendon is anchors, pulleys, guides, and an end
        anchor. Its length includes the wrap arcs.
        """
        with self._lock:
            spec = self._get_assembly(name)
            chosen_motion, chosen_study = _cable_timeline(spec, sequence, motion, study)
            return assembly.check_cables(
                spec,
                self._assembly_shapes(spec),
                chosen_motion,
                study=chosen_study,
                steps=steps,
            )

    def solve_gear_train(self, name: str, drive: dict) -> dict:
        """Speed and torque of every shaft in the assembly gear train.

        Tooth counts come from the generated gears on the train. ``drive`` gives
        the input shaft, speed in rpm, and torque in N·m. A differential in a
        turn also needs one output speed.
        """
        with self._lock:
            spec = self._get_assembly(name)
            gear_specs = {}
            for instance in spec["instances"]:
                part = self._get(instance["part"])
                if part.gear_spec is not None:
                    gear_specs[instance["name"]] = part.gear_spec
            from aicad.gears.train import solve_gear_train as solve_train

            return solve_train(spec, drive, gear_specs)

    def estimate_power(
        self,
        name: str,
        sequence: str | None = None,
        motion: dict | None = None,
        actuators: list[dict] | None = None,
        study: dict | str | None = None,
    ) -> dict:
        """Quasi-static actuator power through a pose, a motion, or a motion study.

        ``sequence`` is a motion, or a motion study when no motion has that name.
        ``study`` is a study name or an inline study.
        """
        with self._lock:
            spec = self._get_assembly(name)
            chosen_motion, chosen_study = _cable_timeline(spec, sequence, motion, study)
            return assembly.estimate_power(
                spec,
                self._assembly_shapes(spec),
                chosen_motion,
                actuators,
                study=chosen_study,
            )

    def solve_modal(self, name: str, setup: dict) -> dict:
        """Natural frequencies of one part. Pictures land next to the other renders."""
        with self._lock:
            part = self._get(name)
            out_dir = _project_directory(self.workspace, self.workspace / "renders" / name, "Render")
            return solve_modal(part.shape, setup, out_dir=out_dir, stem="modal", part_name=name)

    def solve_assembly_modal(self, name: str, setup: dict | None = None) -> dict:
        """Natural frequencies of a posed assembly. Joints are tied and do not flex."""
        with self._lock:
            spec = self._get_assembly(name)
            out_dir = _project_directory(
                self.workspace, self.workspace / "renders" / "assemblies" / spec["name"], "Render"
            )
            return solve_modal_assembly(spec, self._assembly_shapes(spec), setup or {}, out_dir)

    def check_gears(self, spec: dict) -> dict:
        """Centre distance, backlash, contact ratio, undercut, and interference of a gear pair.

        ``spec`` names two gear specs or two gear parts, or an assembly. An
        assembly with a gear train checks each train mesh. Otherwise it checks
        every gear relation, or every pair of gear instances when there is no
        gear relation. The smaller gear is turned through one full rotation.
        One pair that cannot be checked is reported as failed, and the other
        pairs are still checked.
        """
        if not isinstance(spec, dict):
            raise GeometryError("A gear mesh check is an object.")
        with self._lock:
            if spec.get("assembly"):
                return self._check_assembly_gears(spec)
            return self._check_named_gear_pair(spec)

    def _check_named_gear_pair(self, spec: dict) -> dict:
        pinion_spec, pinion_part = self._gear_member(spec.get("pinion"), "pinion")
        gear_spec, gear_part = self._gear_member(spec.get("gear"), "gear")
        return gears_mod.check_pair(
            pinion_spec,
            gear_spec,
            self._gear_mounting(spec, pinion_part, gear_part),
            spec.get("backlash_mm"),
            spec.get("steps"),
        )

    def _check_assembly_gears(self, spec: dict) -> dict:
        from aicad.gears.meshcheck import _LIMITS, mounting_from_frames
        from aicad.honesty import VERIFIED_CHECK, finish

        name = str(spec["assembly"])
        assembly = self._get_assembly(name)
        solved = solve_assembly(assembly)
        frames = {item["name"]: item["matrix"] for item in solved["instances"]}
        selected = self._assembly_gear_pairs(assembly, spec.get("pinion"), spec.get("gear"))
        reports = []
        for left_name, right_name, label in selected:
            left_part = self._instance_gear(assembly, left_name)
            right_part = self._instance_gear(assembly, right_name)
            measured = mounting_from_frames(frames[left_name], frames[right_name])
            mounting = {}
            if measured["parallel"]:
                mounting["centre_distance_mm"] = measured["centre_distance_mm"]
                mounting["shaft_angle_deg"] = measured["shaft_angle_deg"]
                if "pinion_phase_deg" in measured:
                    mounting["pinion_phase_deg"] = measured["pinion_phase_deg"]
                    mounting["gear_phase_deg"] = measured["gear_phase_deg"]
            else:
                mounting["centre_distance_mm"] = measured["centre_distance_mm"]
                mounting["shaft_angle_deg"] = measured["shaft_angle_deg"]
            mounting["pinion_matrix"] = frames[left_name]
            mounting["gear_matrix"] = frames[right_name]
            for key in ("centre_distance_mm", "shaft_angle_deg", "offset_mm", "pinion_phase_deg", "gear_phase_deg"):
                if spec.get(key) is not None:
                    mounting[key] = spec[key]
            try:
                report = gears_mod.check_pair(
                    copy.deepcopy(left_part.gear_spec),
                    copy.deepcopy(right_part.gear_spec),
                    mounting,
                    spec.get("backlash_mm"),
                    spec.get("steps"),
                )
            except GeometryError as exc:
                report = {
                    "status": "fail",
                    "instances": [left_name, right_name],
                    "relation": label,
                    "findings": [str(exc)],
                    "interference": None,
                    "assumptions": [],
                }
            else:
                report["instances"] = [left_name, right_name]
                report["relation"] = label
            reports.append(report)
        failed = [item for item in reports if item.get("status") != "pass"]
        return finish(
            {
                "status": "fail" if failed else "pass",
                "assembly": name,
                "pairs": reports,
                "passed": len(reports) - len(failed),
                "failed": len(failed),
                "assumptions": list(reports[0].get("assumptions") or []),
            },
            kind=VERIFIED_CHECK,
            name=name,
            what=(
                "Mesh and backlash of every gear pair in the assembly. "
                "A pair fails when its backlash, contact ratio, undercut, or interference fails."
            ),
            limits=list(_LIMITS),
        )

    def _assembly_gear_pairs(self, assembly: dict, pinion, gear) -> list[tuple[str, str, str]]:
        train_pairs = self._train_mesh_pairs(assembly)
        if train_pairs:
            if pinion is None and gear is None:
                return train_pairs
            selected = []
            for left, right, label in train_pairs:
                forward = self._instance_named(assembly, left, pinion) and self._instance_named(
                    assembly, right, gear
                )
                backward = self._instance_named(assembly, left, gear) and self._instance_named(
                    assembly, right, pinion
                )
                if forward:
                    selected.append((left, right, label))
                elif backward:
                    selected.append((right, left, label))
            if not selected:
                raise GeometryError(f"Assembly {assembly['name']!r} has no gear mesh for that pair.")
            return selected
        joints = {joint["name"]: joint for joint in assembly["joints"]}
        relations = [item for item in assembly["relations"] if item["type"] == "gear"]
        if relations:
            pairs = []
            for relation in relations:
                driver = joints[relation["driver"]]["child"]
                driven = joints[relation["driven"]]["child"]
                if pinion is None and gear is None:
                    pairs.append((driver, driven, relation["name"]))
                    continue
                forward = self._instance_named(assembly, driver, pinion) and self._instance_named(
                    assembly, driven, gear
                )
                backward = self._instance_named(assembly, driver, gear) and self._instance_named(
                    assembly, driven, pinion
                )
                if forward:
                    pairs.append((driver, driven, relation["name"]))
                elif backward:
                    pairs.append((driven, driver, relation["name"]))
            if not pairs:
                raise GeometryError(f"Assembly {assembly['name']!r} has no gear relation for that pair.")
            return pairs
        names = [
            instance["name"]
            for instance in assembly["instances"]
            if self._parts.get(instance["part"]) and self._parts[instance["part"]].gear_spec
        ]
        if pinion is not None or gear is not None:
            left = self._find_instance(assembly, pinion, "pinion")
            right = self._find_instance(assembly, gear, "gear")
            return [(left, right, f"{left}/{right}")]
        if len(names) < 2:
            raise GeometryError(f"Assembly {assembly['name']!r} has no gear pair to check.")
        pairs = []
        for index, left in enumerate(names):
            for right in names[index + 1 :]:
                pairs.append((left, right, f"{left}/{right}"))
        return pairs

    def _train_mesh_pairs(self, assembly: dict) -> list[tuple[str, str, str]]:
        """Meshing instances from a stored gear train.

        A gear relation names the shaft joints. On a compound shaft the joint's
        child is only one of the gears, so the relation cannot name the mesh.
        The train mesh names both gears.
        """
        train = assembly.get("gear_train")
        if not train:
            return []
        gears = {
            gear["name"]: gear.get("instance")
            for shaft in train.get("shafts") or []
            for gear in shaft.get("gears") or []
        }
        pairs = []
        for mesh in train.get("meshes") or []:
            left = gears.get(mesh.get("a"))
            right = gears.get(mesh.get("b"))
            if not left or not right:
                continue
            if not self._instance_is_gear(assembly, left) or not self._instance_is_gear(assembly, right):
                continue
            pairs.append((left, right, str(mesh.get("name") or f"{left}/{right}")))
        return pairs

    def _instance_is_gear(self, assembly: dict, instance_name: str) -> bool:
        instance = next((item for item in assembly["instances"] if item["name"] == instance_name), None)
        if instance is None:
            return False
        part = self._parts.get(instance["part"])
        return bool(part and part.gear_spec)

    def _instance_named(self, assembly: dict, instance_name: str, wanted) -> bool:
        if wanted is None:
            return True
        if instance_name == wanted:
            return True
        instance = next(item for item in assembly["instances"] if item["name"] == instance_name)
        return instance["part"] == wanted

    def _find_instance(self, assembly: dict, wanted, label: str) -> str:
        if not isinstance(wanted, str):
            raise GeometryError(f"In an assembly, {label} is an instance name or a part name.")
        matches = [
            instance["name"]
            for instance in assembly["instances"]
            if instance["name"] == wanted or instance["part"] == wanted
        ]
        if len(matches) != 1:
            raise GeometryError(f"Assembly {assembly['name']!r} needs one instance for {label} {wanted!r}.")
        return matches[0]

    def _instance_gear(self, assembly: dict, instance_name: str):
        instance = next(item for item in assembly["instances"] if item["name"] == instance_name)
        part = self._get(instance["part"])
        if not part.gear_spec:
            raise GeometryError(
                f"Instance {instance_name!r} uses part {part.name!r}, which is not a generated gear."
            )
        return part

    def _gear_member(self, value, label: str):
        if isinstance(value, str):
            part = self._get(value)
            if not part.gear_spec:
                raise GeometryError(f"{label} {value!r} is not a generated gear.")
            return copy.deepcopy(part.gear_spec), part
        if isinstance(value, dict):
            return value, None
        raise GeometryError(f"{label} must be a gear spec or the name of a gear part.")

    def _gear_mounting(self, spec: dict, pinion_part, gear_part) -> dict:
        mounting = {}
        for key in ("centre_distance_mm", "shaft_angle_deg", "offset_mm", "pinion_phase_deg", "gear_phase_deg"):
            if spec.get(key) is not None:
                mounting[key] = spec[key]
        if (
            pinion_part is not None
            and gear_part is not None
            and "centre_distance_mm" not in mounting
            and "offset_mm" not in mounting
        ):
            from aicad.gears.meshcheck import mounting_from_frames
            from aicad.kinematics import matrix_from_placement

            measured = mounting_from_frames(
                matrix_from_placement(pinion_part.placement),
                matrix_from_placement(gear_part.placement),
            )
            mounting.setdefault("centre_distance_mm", measured["centre_distance_mm"])
            mounting.setdefault("shaft_angle_deg", measured["shaft_angle_deg"])
            if "pinion_phase_deg" in measured:
                mounting.setdefault("pinion_phase_deg", measured["pinion_phase_deg"])
                mounting.setdefault("gear_phase_deg", measured["gear_phase_deg"])
        if pinion_part is not None and gear_part is not None:
            mounting.setdefault("pinion_position_mm", list(pinion_part.placement["position"]))
            mounting.setdefault("pinion_rotation_deg", list(pinion_part.placement["rotation_deg"]))
            mounting.setdefault("gear_position_mm", list(gear_part.placement["position"]))
            mounting.setdefault("gear_rotation_deg", list(gear_part.placement["rotation_deg"]))
        return mounting

    def check_bolts(self, spec: dict) -> dict:
        """Preload, torque, slip, and loosening for a fastener. A library part can supply the size."""
        if not isinstance(spec, dict):
            raise GeometryError("A bolt check is an object with size, grade, and loads.")
        with self._lock:
            merged = dict(spec)
            part_name = merged.get("part")
            if part_name:
                part = self._get(str(part_name))
                library_spec = part.library_spec or {}
                if not merged.get("size") and library_spec.get("size"):
                    merged["size"] = library_spec["size"]
                if merged.get("grip_mm") is None and merged.get("length") is None and library_spec.get("length"):
                    merged["grip_mm"] = library_spec["length"]
                merged["library_kind"] = library_spec.get("kind")
            report = check_joint(merged)
            if part_name:
                report["part"] = part_name
            if merged.get("assembly"):
                report["assembly"] = merged["assembly"]
            return report

    def check_fatigue(self, spec: dict) -> dict:
        """Goodman check of a stated stress amplitude. It does not mesh the part."""
        return fatigue_check(spec)

    def check_print(self, name: str, setup: dict | None = None) -> dict:
        """Overhang map, a suggested orientation, printer fit, and an optional slicer estimate."""
        with self._lock:
            part = self._get(name)
            out_dir = _project_directory(self.workspace, self.workspace / "renders" / name, "Render")
            return analyze_print(part.shape, setup or {}, out_dir, name)

    def list_printers(self) -> dict:
        return printer_catalog()

    def export_robot(self, name: str, out_dir: str | Path | None = None) -> dict:
        """Write URDF and MJCF for an assembly, then try to load and step the result."""
        with self._lock:
            spec = self._get_assembly(name)
            if out_dir:
                from aicad.sandbox import require_in_project

                require_in_project(out_dir, self.workspace, writing=True)
                destination = out_dir
            else:
                destination = self.workspace / "exports" / "robot" / spec["name"]
            destination = _project_directory(self.workspace, Path(destination), "Robot export")
            return export_robot(spec, self._assembly_shapes(spec), destination)

    def render_assembly(self, name: str) -> dict:
        """One isometric picture of the assembly in its stored pose."""
        with self._lock:
            spec = self._get_assembly(name)
            shapes = self._assembly_shapes(spec)
            solved, _placed = assembly.posed_shapes(spec, shapes)
            mesh = assembly._colored_mesh(shapes, solved)  # noqa: SLF001
            out = _project_directory(
                self.workspace, self.workspace / "renders" / "assemblies" / spec["name"], "Render"
            )
            pictures = render_motion_frames(
                [mesh],
                out,
                "preview",
                [spec["name"]],
            )
            return {"name": spec["name"], "solved": solved, "pictures": pictures}

    def export_drawing(
        self,
        name: str,
        scale: str | None = None,
        exploded: bool = False,
        gap_mm: float | None = None,
        out_dir: str | Path | None = None,
    ) -> dict:
        """Write a third-angle sheet of an assembly as PDF, DXF, and PNG.

        Dimensions are the overall width, depth, and height of the posed
        solids. The sheet is a layout drawing: it has no GD&T and no
        tolerances. ``scale`` is a ratio such as ``1:2``. ``exploded`` draws
        the isometric view with the parts pulled apart.
        """
        with self._lock:
            spec = self._get_assembly(name)
            shapes = self._assembly_shapes(spec)
            solved, _placed = assembly.posed_shapes(spec, shapes)
            planned = None
            offsets = None
            gap = gap_mm
            if exploded:
                planned = explode_mod.plan(spec, solved, shapes, self._library_specs(spec), gap_mm)
                offsets = planned["offsets"]
                gap = planned["gap_mm"]
            if out_dir:
                from aicad.sandbox import require_in_project

                require_in_project(out_dir, self.workspace, writing=True)
                checked = Path(out_dir)
                if not checked.is_absolute():
                    from aicad.paths import require_export_path

                    checked = require_export_path(self.workspace, out_dir, "Export path", GeometryError)
                destination = _project_directory(self.workspace, checked, "Export path")
            else:
                destination = _project_directory(
                    self.workspace, self.workspace / "exports" / "drawings", "Drawing"
                )
            stem = _output_stem(spec["name"])
            png = _project_file(
                self.workspace, self.workspace / "renders" / "assemblies" / stem / "drawing.png", "Drawing"
            )
            report = drawing_mod.make_drawing(
                spec,
                solved,
                shapes,
                pdf_path=destination / f"{stem}.pdf",
                dxf_path=destination / f"{stem}.dxf",
                png_path=png,
                scale=scale,
                offsets=offsets,
                gap_mm=gap,
            )
            extras = []
            if planned is not None and planned.get("gap_capped"):
                extras.append("The automatic gap is capped at 1000000 mm.")
            if extras:
                report["note"] = f"{report.get('note') or ''} {' '.join(extras)}".strip()
            return report

    def bill_of_materials(self, name: str, out: str | Path | None = None) -> dict:
        """Count parts, catalog fasteners, and hoses or cables. Also writes a CSV.

        Quantities come from the instances. There are no prices. A mass is the
        solid's volume times the handbook density, and the row says so.
        """
        with self._lock:
            spec = self._get_assembly(name)
            shapes = self._assembly_shapes(spec)
            report = bom_mod.build_bom(spec, shapes, self._part_facts(spec))
            stem = _output_stem(spec["name"])
            if out:
                from aicad.sandbox import require_in_project

                require_in_project(out, self.workspace, writing=True)
                checked = Path(out)
                if not checked.is_absolute():
                    from aicad.paths import require_export_path

                    checked = require_export_path(self.workspace, out, "Export path", GeometryError)
                csv_path = _project_file(self.workspace, checked, "Export path")
            else:
                csv_path = _project_file(
                    self.workspace, self.workspace / "exports" / "bom" / f"{stem}.csv", "Bill of materials"
                )
            png = _project_file(
                self.workspace, self.workspace / "renders" / "assemblies" / stem / "bom.png", "Bill of materials"
            )
            bom_mod.write_csv(report, csv_path)
            view = bom_mod.render_table(report, png)
            report["csv"] = str(csv_path.resolve())
            report["views"] = [view]
            return report

    def exploded_view(
        self,
        name: str,
        gap_mm: float | None = None,
        on_drawing: bool = False,
        out_dir: str | Path | None = None,
    ) -> dict:
        """Render the assembly pulled apart along its joints.

        The grounded part stays. Each child moves away from its parent by one
        gap, and the shift adds up along the tree. ``on_drawing`` also writes
        the layout sheet with that exploded isometric. The gap is not a dimension.
        """
        with self._lock:
            spec = self._get_assembly(name)
            shapes = self._assembly_shapes(spec)
            solved, _placed = assembly.posed_shapes(spec, shapes)
            planned = explode_mod.plan(spec, solved, shapes, self._library_specs(spec), gap_mm)
            moved = explode_mod.apply_moves(solved, planned["offsets"])
            mesh = assembly._colored_mesh(shapes, moved)  # noqa: SLF001
            stem = _output_stem(spec["name"])
            out = _project_directory(
                self.workspace, self.workspace / "renders" / "assemblies" / stem, "Render"
            )
            try:
                pictures = render_motion_frames(
                    [mesh],
                    out,
                    "exploded",
                    [f"{spec['name']}  exploded"],
                )
            except OSError as exc:
                raise GeometryError(
                    f"Could not write the exploded picture for {spec['name']}. {exc.strerror or exc}."
                ) from exc
            frame = pictures["frames"][0]
            from aicad.blueprint import png_size

            width, height = png_size(frame["path"])
            report = {key: value for key, value in planned.items() if key != "offsets"}
            report["views"] = [
                {"name": "exploded", "path": frame["path"], "width": width, "height": height}
            ]
            report["pictures"] = pictures
            if on_drawing:
                if out_dir:
                    from aicad.sandbox import require_in_project

                    require_in_project(out_dir, self.workspace, writing=True)
                sheet = self.export_drawing(
                    name,
                    exploded=True,
                    gap_mm=planned["gap_mm"],
                    out_dir=out_dir,
                )
                report["drawing"] = {
                    "pdf": sheet["files"]["pdf"],
                    "dxf": sheet["files"]["dxf"],
                    "png": sheet["files"]["png"],
                    "scale": sheet["scale"],
                    "result_kind": sheet["result_kind"],
                    "result_label": sheet["result_label"],
                }
                report["views"].extend(sheet["views"])
            from aicad.honesty import VERIFIED_CHECK, finish

            finished = finish(
                report,
                kind=VERIFIED_CHECK,
                name="Exploded view",
                what=(
                    "This picture moves each part by a stated gap along its joint. "
                    "The positions are the posed model plus that shift. "
                    "It is not a disassembly procedure, and the gaps are not dimensions."
                ),
                limits=list(report.get("omits") or explode_mod.OMITS),
            )
            return finished

    def render_animation(
        self,
        name: str,
        kind: str | None = None,
        sequence: str | None = None,
        study: dict | None = None,
        frames: int | None = None,
        fps: float | None = None,
        duration_s: float | None = None,
        width: int | None = None,
        height: int | None = None,
        camera: str | dict | None = None,
        camera_path: list | None = None,
        turns: float | None = None,
        elevation_deg: float | None = None,
        background: str | None = None,
        colors: dict | None = None,
        materials: dict | None = None,
        overlay: str | None = None,
        joint: str | None = None,
        gap_mm: float | None = None,
        video: str | None = None,
        path: str | Path | None = None,
    ) -> dict:
        """Write a GIF of this assembly, and a video when ffmpeg is on PATH.

        ``kind`` is ``motion``, ``orbit``, or ``exploded``. ``frames`` counts
        both ends. ``fps`` is the playback rate. ``width`` and ``height`` are
        even pixel sizes. ``camera`` is a view name or ``orbit``.
        ``camera_path`` is a list of ``{azimuth_deg, elevation_deg}``.
        ``background`` is ``paper``, ``white``, ``black``, or ``#rrggbb``.
        ``colors`` and ``materials`` recolor the pictures only. ``overlay`` is
        ``none``, ``step``, ``time``, or ``value``. ``video`` is ``gif``,
        ``mp4``, ``webm``, or ``both``. ``path`` is a file or a directory
        inside the project. A missing ffmpeg still leaves the GIF, and
        ``video_message`` says why the video was not written.
        """
        with self._lock:
            if path is not None:
                from aicad.animation import refuse_output

                refuse_output(self.workspace, path)
            spec = self._get_assembly(name)
            from aicad.animation import render_clip

            try:
                return render_clip(
                    root=self.workspace,
                    spec=spec,
                    shapes=self._assembly_shapes(spec),
                    libraries=self._library_specs(spec),
                    kind=kind,
                    sequence=sequence,
                    study=study,
                    frames=frames,
                    fps=fps,
                    duration_s=duration_s,
                    width=width,
                    height=height,
                    camera=camera,
                    camera_path=camera_path,
                    turns=turns,
                    elevation_deg=elevation_deg,
                    background=background,
                    colors=colors,
                    materials=materials,
                    overlay=overlay,
                    joint=joint,
                    gap_mm=gap_mm,
                    video=video,
                    path=path,
                )
            except OSError as exc:
                raise GeometryError(
                    f"Could not write the animation for {spec['name']}. {exc.strerror or exc}."
                ) from exc

    def assembly_mesh(self, name: str) -> dict:
        with self._lock:
            spec = self._get_assembly(name)
            return assembly.preview_mesh(spec, self._assembly_shapes(spec))

    def _commit_assembly(self, spec: dict) -> dict:
        cleaned = normalize_assembly(self._with_resolved_mates(spec))
        self._require_assembly_parts(cleaned)
        if cleaned["actuators"]:
            actuators_for(cleaned)
        solved = solve_assembly(cleaned)
        by_name = {row["name"]: row for row in solved["joints"]}
        for joint in cleaned["joints"]:
            row = by_name[joint["name"]]
            joint["value"] = row["value"]
            joint["slide"] = row["slide"]
        self._assemblies[cleaned["name"]] = cleaned
        self._save()
        return {"assembly": copy.deepcopy(cleaned), "solved": solved}

    def _get_assembly(self, name: str) -> dict:
        _validate_name(name)
        spec = self._assemblies.get(name)
        if spec is None:
            known = ", ".join(self._assemblies) or "(none)"
            raise GeometryError(f"No assembly named {name!r}. Assemblies: {known}.")
        return spec

    def _require_assembly_parts(self, spec: dict) -> None:
        missing = []
        for instance in spec["instances"]:
            if instance["part"] not in self._parts and instance["part"] not in missing:
                missing.append(instance["part"])
        if missing:
            known = ", ".join(self._parts) or "(none)"
            raise GeometryError(
                f"Assembly {spec['name']!r} needs parts that are not in the document: "
                f"{', '.join(missing)}. Parts: {known}."
            )

    def _assembly_shapes(self, spec: dict) -> dict:
        shapes = {}
        for instance in spec["instances"]:
            part_name = instance["part"]
            if part_name not in shapes:
                shapes[part_name] = self._get(part_name).base
        return shapes

    def _library_specs(self, spec: dict) -> dict[str, dict | None]:
        specs: dict[str, dict | None] = {}
        for instance in spec["instances"]:
            part_name = instance["part"]
            if part_name not in specs:
                specs[part_name] = copy.deepcopy(self._get(part_name).library_spec)
        return specs

    def _part_facts(self, spec: dict) -> dict[str, dict]:
        facts: dict[str, dict] = {}
        for instance in spec["instances"]:
            part = self._get(instance["part"])
            if part.name not in facts:
                facts[part.name] = {
                    "source": part.source,
                    "library": copy.deepcopy(part.library_spec) if part.library_spec else None,
                }
        return facts
    def start_plan(self, name: str, title: str | None = None, summary: str = "", bundle: dict | None = None) -> dict:
        """Start a plan. Research, sheets, and questions are data, not a solid."""
        with self._lock:
            return self._plan_store().start(name, title, summary=summary, bundle=bundle)

    def list_plans(self) -> dict:
        """Names, status, and revision of every plan in this project."""
        with self._lock:
            return {"units": "mm", "plans": self._plan_store().summaries()}

    def get_plan(self, name: str) -> dict:
        """One plan, including every revision and the current blueprint sheets."""
        with self._lock:
            return self._plan_store().get(name)

    def add_plan_research(self, name: str, note: str, source: str, title: str = "") -> dict:
        """Add a research note. A source is required. The current revision must be a draft."""
        with self._lock:
            return self._plan_store().add_research(name, note, source, title=title)

    def add_plan_assumption(self, name: str, text: str) -> dict:
        """Add an assumption for the person to accept or reject."""
        with self._lock:
            return self._plan_store().add_assumption(name, text)

    def add_plan_question(self, name: str, text: str) -> dict:
        """Add an open question for the person to answer."""
        with self._lock:
            return self._plan_store().add_question(name, text)

    def put_plan_sheet(self, name: str, spec: dict) -> dict:
        """Draw or replace one blueprint sheet of blocks, dimensions, and labels."""
        with self._lock:
            return self._plan_store().put_sheet(name, spec)

    def set_plan_dimensions(self, name: str, dimensions: list[dict]) -> dict:
        """Set the named millimeters the modeling step will use after approval."""
        with self._lock:
            return self._plan_store().set_dimensions(name, dimensions)

    def comment_on_plan(self, name: str, item: str, text: str) -> dict:
        """Comment on the plan or on one assumption, question, or sheet."""
        with self._lock:
            return self._plan_store().comment(name, item, text)

    def answer_plan_question(self, name: str, item: str, text: str) -> dict:
        """Answer a question. Only the person can do this, never the agent."""
        with self._lock:
            return self._plan_store().answer(name, item, text)

    def mark_plan_item(self, name: str, item: str, mark: str) -> dict:
        """Accept or reject an assumption, or waive a question. Only the person can do this."""
        with self._lock:
            return self._plan_store().mark(name, item, mark)

    def submit_plan(self, name: str) -> dict:
        """Move a draft to in review. Approval is a separate step, and only the person can take it."""
        with self._lock:
            return self._plan_store().submit(name)

    def revise_plan(self, name: str, changed: str, bundle: dict | None = None) -> dict:
        """Keep the current revision and open the next one. Say what changed."""
        with self._lock:
            return self._plan_store().revise(name, changed, bundle=bundle)

    def approve_plan(self, name: str) -> dict:
        """Approve the current revision. Only the person can do this, never the agent."""
        with self._lock:
            return self._plan_store().approve(name)

    def start_model_from_plan(self, name: str) -> dict:
        """Hand the approved dimensions and assumptions to modeling. Refuses until the person approves."""
        with self._lock:
            return self._plan_store().start_work(name, "model")

    def start_simulation_from_plan(self, name: str) -> dict:
        """Hand the approved plan to simulation. Refuses until the person approves."""
        with self._lock:
            return self._plan_store().start_work(name, "simulation")

    def _plan_store(self):
        from aicad.plan import PlanStore

        return PlanStore(self.workspace, actor=self.actor)

    def session_payload(self) -> dict:
        """The serializable document. Undo stores this and restores it."""
        with self._lock:
            return self._payload()

    def replace_session(self, payload: dict) -> None:
        """Rebuild every part from a payload previously returned by :meth:`session_payload`."""
        with self._lock:
            self._parts.clear()
            self._sketches.clear()
            self._assemblies.clear()
            self._apply_payload(payload)
            self._save()

    def _session_path(self) -> Path:
        return self.workspace / "session.json"

    def _payload(self) -> dict:
        return {
            "units": "mm",
            "sketches": [
                {"name": sketch.name, "spec": copy.deepcopy(sketch.spec)} for sketch in self._sketches.values()
            ],
            "parts": [
                {
                    "name": part.name,
                    "source": part.source,
                    "script": part.script,
                    "ops": copy.deepcopy(part.ops) if part.ops is not None else None,
                    "step_file": part.step_file,
                    "library": copy.deepcopy(part.library_spec) if part.library_spec is not None else None,
                    "gear": copy.deepcopy(part.gear_spec) if part.gear_spec is not None else None,
                    "placement": copy.deepcopy(part.placement),
                    "sketch": part.sketch_name,
                    "steps": copy.deepcopy(part.sketch_steps) if part.sketch_steps is not None else None,
                    "geometry_file": part.geometry_file,
                    "script_sha256": part.script_sha256,
                    "features": part._shape.feature_report().to_dict() if part.source == "script" and part._shape is not None else None,
                }
                for part in self._parts.values()
            ],
            "assemblies": [copy.deepcopy(spec) for spec in self._assemblies.values()],
        }

    def _save(self) -> None:
        from aicad.paths import publish_text

        payload = self._payload()
        path = self._session_path()
        try:
            text = json.dumps(payload, indent=2, allow_nan=False) + "\n"
        except ValueError as exc:
            raise DocumentError(
                "The session has a number that is not finite, so it was not saved."
            ) from exc
        publish_text(path, text)

    def _reload(self) -> None:
        """Read ``session.json`` again. Script text is loaded and not executed."""
        with self._lock:
            self._parts.clear()
            self._sketches.clear()
            self._assemblies.clear()
            self._load()

    def _store_script_geometry(self, name: str, script: str, shape: kernel.Shape) -> str:
        relative = f".aicad/geometry/{name}-{_script_hash(script)[:16]}.step"
        path = _project_file(self.workspace, self.workspace / relative, "Script geometry")
        kernel.export_step_file(shape, path)
        return relative

    def _load(self) -> None:
        path = self._session_path()
        if not path.exists() and not path.is_symlink():
            return
        path = _project_resolved(self.workspace, path, "Session file", DocumentError)
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DocumentError(f"Could not read {path}: {exc}") from exc
        self._apply_payload(payload)

    def _apply_payload(self, payload: dict) -> None:
        self.problems = []
        for record in payload.get("sketches", []):
            self._sketches[record["name"]] = _Sketch(record["name"], record["spec"])
        for record in payload.get("parts", []):
            name = str(record.get("name") or "")
            source = record.get("source", "script")
            placement = record.get("placement") or _identity_placement()
            script_text = record.get("script") or ""
            script_pending = False
            try:
                position = kernel.finite_vec3(placement.get("position", [0.0, 0.0, 0.0]), "position")
                rotation = kernel.finite_vec3(
                    placement.get("rotation_deg", [0.0, 0.0, 0.0]), "rotation_deg", unit="deg"
                )
                if source == "ops":
                    base = kernel.make_from_ops(record["ops"])
                elif source == "step":
                    step_path = _session_project_file(
                        self.workspace, record.get("step_file"), f"Part {name!r} step file"
                    )
                    base = kernel.import_step_file(step_path)
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
                elif source == "gear":
                    spec = record.get("gear")
                    if not isinstance(spec, dict):
                        raise DocumentError(f"Part {name!r} is a gear but has no gear spec.")
                    base = kernel.adopt(gears_mod.build_gear(spec))
                elif source == "sketch":
                    sketch_name = record.get("sketch")
                    sketch = self._sketches.get(sketch_name)
                    if sketch is None:
                        raise DocumentError(
                            f"Part {name!r} needs sketch {sketch_name!r}, which is not in the session."
                        )
                    solved = solve_sketch(sketch.spec, sketch.name)
                    if solved.status in _UNBUILDABLE:
                        raise DocumentError(f"Could not rebuild part {name!r}: {solved.note}")
                    base = kernel.solid_from_sketch(solved.geometry, record.get("steps") or [])
                else:
                    source = "script"
                    script_hash = _script_hash(script_text)
                    stored_hash = record.get("script_sha256")
                    script_pending = not isinstance(stored_hash, str) or script_hash != stored_hash
                    geometry = record.get("geometry_file")
                    stored = geometry.replace("\\", "/") if isinstance(geometry, str) else ""
                    prefix = stored_hash[:16] if isinstance(stored_hash, str) else ""
                    expected = f".aicad/geometry/{name}-{prefix}.step" if prefix else ""
                    # Every recorded path still goes through the project guard, so a
                    # link that leaves the project is refused. Only the file named
                    # for this script's stored hash is imported.
                    if stored:
                        geometry_path = _session_project_file(
                            self.workspace, geometry, f"Part {name!r} geometry file"
                        )
                        if stored == expected and geometry_path.is_file():
                            base = kernel.import_step_file(geometry_path)
                            base.features = feature_report_from_dict(record.get("features"))
                        else:
                            base = None
                            script_pending = True
                    else:
                        # A downloaded script is shown, not run.
                        base = None
                        script_pending = True
                if base is None:
                    shape = None
                else:
                    shape = kernel.place(base, position, rotation)
                    if source == "script":
                        shape.features = base.features
                stored_placement = {
                    "position": [position[0], position[1], position[2]],
                    "rotation_deg": [rotation[0], rotation[1], rotation[2]],
                }
                rebuild_error = None
            except Exception as exc:
                message = f"Part {name!r} was not rebuilt: {exc}"
                self.problems.append(message)
                base = None
                shape = None
                stored_placement = _identity_placement()
                script_pending = True
                rebuild_error = message
            self._parts[name] = _Part(
                name,
                source,
                shape,
                script=record.get("script"),
                ops=record.get("ops"),
                step_file=record.get("step_file") if isinstance(record.get("step_file"), str) else None,
                base=base,
                placement=stored_placement,
                library_spec=record.get("library"),
                gear_spec=record.get("gear") if source == "gear" else None,
                sketch_name=record.get("sketch") if source == "sketch" else None,
                sketch_steps=record.get("steps") if source == "sketch" else None,
                geometry_file=record.get("geometry_file") if source == "script" else None,
                script_sha256=record.get("script_sha256") if source == "script" else None,
                script_pending=script_pending,
                rebuild_error=rebuild_error,
            )
        self._assemblies.clear()
        for record in payload.get("assemblies", []):
            cleaned = normalize_assembly(self._with_resolved_mates(record))
            self._require_assembly_parts(cleaned)
            self._assemblies[cleaned["name"]] = cleaned

    def _with_resolved_mates(self, spec: dict) -> dict:
        """Turn parent_mate and child_mate into anchors before the joint is stored.

        The mate names stay on the joint. Opening the project resolves them
        again from the catalog part on each instance.
        """
        if not isinstance(spec, dict):
            return spec
        joints = spec.get("joints") or []
        if not isinstance(joints, list):
            return spec
        if not any(
            isinstance(joint, dict) and (joint.get("parent_mate") or joint.get("child_mate"))
            for joint in joints
        ):
            return spec
        part_by_instance: dict[str, str] = {}
        instances = spec.get("instances") or []
        if isinstance(instances, list):
            for instance in instances:
                if isinstance(instance, dict) and instance.get("name") and instance.get("part"):
                    part_by_instance[str(instance["name"])] = str(instance["part"])
        needed: list[str] = []
        for joint in joints:
            if not isinstance(joint, dict):
                continue
            for side in ("parent", "child"):
                if not joint.get(f"{side}_mate"):
                    continue
                part_name = part_by_instance.get(str(joint.get(side) or ""))
                if part_name and part_name not in needed:
                    needed.append(part_name)
        missing = [name for name in needed if name not in self._parts]
        if missing:
            known = ", ".join(self._parts) or "(none)"
            label = spec.get("name") or "assembly"
            raise GeometryError(
                f"Assembly {label!r} needs parts that are not in the document: "
                f"{', '.join(missing)}. Parts: {known}."
            )
        specs = {name: copy.deepcopy(self._parts[name].library_spec) for name in needed}
        return library.resolve_assembly_mates(spec, specs)


def _cable_timeline(spec: dict, sequence, motion, study):
    """A stored motion, or a motion study when that is what the name refers to."""
    if study is not None and motion is not None:
        raise GeometryError("Pass a motion or a motion study, not both.")
    if isinstance(study, str):
        return None, study_by_name(spec, study, None)
    if isinstance(study, dict):
        return None, study_by_name(spec, sequence, study)
    if motion is not None:
        return assembly.motion_by_name(spec, sequence, motion), None
    if not sequence:
        return None, None
    motions = spec.get("motions") or {}
    studies = spec.get("studies") or {}
    if sequence in motions and sequence in studies:
        raise GeometryError(
            f"{sequence!r} is both a motion and a motion study. Pass the motion or the study on its own."
        )
    if sequence in studies:
        return None, study_by_name(spec, sequence, None)
    if sequence in motions:
        return assembly.motion_by_name(spec, sequence, None), None
    known_motions = ", ".join(motions) or "(none)"
    known_studies = ", ".join(studies) or "(none)"
    raise GeometryError(
        f"No motion or motion study {sequence!r}. Motions: {known_motions}. Studies: {known_studies}."
    )


def _unbuilt_message(name: str) -> str:
    return (
        f"Part {name!r} has a script that has not been run. Opening a project does not run scripts. "
        "Run the script when you want the solid."
    )


def _script_hash(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


def _assembly_name(spec: dict) -> str:
    if not isinstance(spec, dict) or not isinstance(spec.get("name"), str):
        raise GeometryError("An assembly spec needs a name.")
    _validate_name(spec["name"])
    return spec["name"]


def _output_stem(name: str) -> str:
    """A file stem that is not a Windows device name. A reserved name is refused."""
    from aicad.paths import is_reserved_device_name

    stem = str(name).strip().rstrip(" .") or "assembly"
    if is_reserved_device_name(stem):
        raise GeometryError(f"{stem} is reserved on Windows. Pick another name.")
    return stem


def _pack_vec(point: tuple[float, float, float]) -> Vec3:
    return Vec3(round_mm(point[0]), round_mm(point[1]), round_mm(point[2]))


def _packaging_joint_value(name: str, value):
    """A number, or a cylindrical ``{value, slide}`` override."""
    if isinstance(value, dict):
        cleaned = {}
        if "value" in value:
            cleaned["value"] = kernel.finite_number(value["value"], f"joint {name}", unit="deg")
        if "slide" in value:
            cleaned["slide"] = kernel.finite_number(value["slide"], f"joint {name} slide", unit="mm")
        if not cleaned or any(key not in {"value", "slide"} for key in value):
            raise GeometryError(
                f"Joint {name} override is a number, or {{value: degrees, slide: millimetres}}."
            )
        return cleaned
    return kernel.finite_number(value, f"joint {name}", unit="deg or mm")


def _packaging_overrides(spec: dict, pose: str | None, joints: dict | None) -> dict:
    overrides: dict = {}
    if pose is not None:
        if not isinstance(pose, str) or not pose.strip():
            raise GeometryError("pose must be a name.")
        if pose not in spec["poses"]:
            known = ", ".join(spec["poses"]) or "(none)"
            raise GeometryError(f"No pose {pose!r} on {spec['name']!r}. Poses: {known}.")
        overrides.update({str(key): float(value) for key, value in spec["poses"][pose].items()})
    if joints is not None:
        if not isinstance(joints, dict):
            raise GeometryError("joints must be an object of joint names and values.")
        for key, value in joints.items():
            overrides[str(key)] = _packaging_joint_value(key, value)
    known = {joint["name"] for joint in spec["joints"]}
    unknown = [name for name in overrides if name not in known]
    if unknown:
        listed = ", ".join(sorted(known)) or "(none)"
        raise GeometryError(
            f"Unknown joint {unknown[0]!r} on {spec['name']!r}. Joints: {listed}."
        )
    return overrides


def _validate_name(name: str) -> None:
    from aicad.paths import is_reserved_device_name

    if not isinstance(name, str) or not _NAME.match(name):
        raise GeometryError(
            "Part names must start with a letter or underscore and contain only "
            "letters, numbers, underscores, and hyphens (64 characters max)."
        )
    if is_reserved_device_name(name):
        raise GeometryError(f"{name} is reserved on Windows. Pick another name.")


def _copy_spec(spec: Sketch | dict) -> dict:
    if isinstance(spec, Sketch):
        spec = spec.to_spec()
    if not isinstance(spec, dict):
        raise GeometryError("A sketch spec is an object with entities and constraints.")
    return copy.deepcopy(spec)


def _identity_placement() -> dict[str, list[float]]:
    return {"position": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]}


def _vec3(value, label: str) -> tuple[float, float, float]:
    unit = "deg" if "rot" in label else "mm"
    return kernel.finite_vec3(value, label, unit=unit)


def _step_import_source(root: Path, raw: str | Path) -> Path:
    """The file ``import_step`` reads.

    An absolute path is a file the person chose. A relative path stays inside
    the project after the link is followed.
    """
    from aicad.paths import stays_inside

    original = Path(raw)
    given = original if original.is_absolute() else root / original
    source = given.resolve()
    root_resolved = root.resolve()
    try:
        given.absolute().relative_to(root_resolved)
        lexical_inside = ".." not in given.parts
    except ValueError:
        lexical_inside = False
    if not original.is_absolute() or lexical_inside:
        if not stays_inside(root_resolved, source):
            raise DocumentError(f"STEP file {raw} is outside the project. It was not read.")
    return source


def _project_file(root: Path, raw: object, label: str, *, allow_absolute_outside: bool = False) -> Path:
    """A path to write that stays inside the project unless an absolute path was named."""
    from aicad.paths import output_path

    return output_path(root, raw, label, GeometryError, allow_absolute_outside=allow_absolute_outside)


def _position_request_has_fea(request: dict) -> bool:
    links = request.get("links")
    if not isinstance(links, dict):
        return False
    return any(isinstance(item, dict) and "fea" in item for item in links.values())


def _project_directory(root: Path, path: Path, label: str) -> Path:
    """A directory inside the project. A link that leaves the project is refused."""
    from aicad.paths import output_dir

    return output_dir(root, path, label, GeometryError)


def _project_resolved(root: Path, path: Path, label: str, error: type[Exception] = GeometryError) -> Path:
    """An existing path inside the project. Nothing is created."""
    from aicad.paths import resolve_in_project

    return resolve_in_project(root, path, label, error)


def _session_project_file(root: Path, raw: object, label: str) -> Path:
    """A session path that stays inside the project. Absolute and ``..`` are not read."""
    from aicad.paths import require_session_file

    return require_session_file(root, raw, label, DocumentError)


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
