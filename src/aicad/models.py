"""Plain records returned by the public API.

Nothing in this module knows about the geometry kernel. Numbers are in
millimeters, and values are rounded so an assistant is not distracted by
solver noise.
"""

from __future__ import annotations

from dataclasses import dataclass

from aicad.honesty import ROUGH_ESTIMATE, VERIFIED_CHECK, finish, slim, weakest


def round_mm(value: float, digits: int = 6) -> float:
    """Round a measured length, area, or volume."""
    return round(float(value), digits)


_MILLIMETERS = frozenset(
    {"mm", "millimeter", "millimeters", "millimetre", "millimetres"}
)


def require_millimeters(value, label: str) -> None:
    """Refuse a length unit other than millimeters.

    A sketch or an assembly that says inches is not converted. Leaving the
    numbers as millimeters would build a part 25.4 times too small.
    """
    if value is None or value == "":
        return
    token = "".join(str(value).strip().lower().split()).rstrip(".")
    if token in _MILLIMETERS:
        return
    from aicad.errors import GeometryError

    raise GeometryError(
        f"{label} uses millimeters. {value!r} is not a length it can use. One inch is 25.4."
    )


@dataclass(frozen=True)
class Vec3:
    x: float
    y: float
    z: float

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass(frozen=True)
class BoundingBox:
    min: Vec3
    max: Vec3
    size: Vec3

    def to_dict(self) -> dict[str, dict[str, float]]:
        return {
            "min": self.min.to_dict(),
            "max": self.max.to_dict(),
            "size": self.size.to_dict(),
        }


@dataclass(frozen=True)
class Measurement:
    """What an assistant should read after a modeling step."""

    units: str
    valid: bool
    watertight: bool
    solid_count: int
    face_count: int
    edge_count: int
    vertex_count: int
    volume_mm3: float
    area_mm2: float
    center_of_mass_mm: Vec3
    bounding_box: BoundingBox
    mass_g: float | None = None
    material: str | None = None
    density_g_cm3: float | None = None

    def to_dict(self) -> dict[str, object]:
        assumptions = [
            "Units are millimeters.",
            "Volume, area, and the center of mass come from the geometry kernel.",
            "Watertight means the kernel reports a valid solid whose edges each belong to two faces.",
        ]
        if self.mass_g is not None:
            assumptions.append("Mass is the model volume times the chosen material density.")
            mass_limit = (
                "Mass is this volume times the chosen handbook density. "
                "It is a rough estimate, not a weighed part."
            )
        else:
            mass_limit = (
                "Mass is not included. Mass is this volume times a handbook density, "
                "and that number is a rough estimate."
            )
        payload: dict[str, object] = {
            "units": self.units,
            "valid": self.valid,
            "watertight": self.watertight,
            "solid_count": self.solid_count,
            "face_count": self.face_count,
            "edge_count": self.edge_count,
            "vertex_count": self.vertex_count,
            "volume_mm3": self.volume_mm3,
            "area_mm2": self.area_mm2,
            "center_of_mass_mm": self.center_of_mass_mm.to_dict(),
            "bounding_box": self.bounding_box.to_dict(),
            "assumptions": assumptions,
        }
        if self.mass_g is not None:
            payload["mass_g"] = self.mass_g
            payload["material"] = self.material
            payload["density_g_cm3"] = self.density_g_cm3
        return finish(
            payload,
            kind=VERIFIED_CHECK,
            name="Model measurement",
            what="Volume, area, center of mass, the bounding box, and the watertight check are properties of the CAD model.",
            limits=[
                "This is the model, not a part weighed or measured with calipers.",
                mass_limit,
                "The kernel's own tolerance applies.",
            ],
        )


@dataclass(frozen=True)
class WallEstimate:
    """Kernel result before a threshold is applied."""

    minimum_mm: float | None
    ray_mm: float | None
    section_mm: float | None
    method: str
    sample_count: int
    samples_per_edge: int
    section_count: int
    thinnest_point_mm: Vec3 | None
    note: str
    capped: bool = False


@dataclass(frozen=True)
class WallReport:
    estimated_min_mm: float | None
    threshold_mm: float | None
    passed: bool | None
    sample_count: int
    samples_per_edge: int
    thinnest_point_mm: Vec3 | None
    method: str
    note: str
    ray_estimate_mm: float | None = None
    section_estimate_mm: float | None = None
    section_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return finish(
            {
                "estimated_min_mm": self.estimated_min_mm,
                "threshold_mm": self.threshold_mm,
                "passed": self.passed,
                "sample_count": self.sample_count,
                "samples_per_edge": self.samples_per_edge,
                "thinnest_point_mm": None
                if self.thinnest_point_mm is None
                else self.thinnest_point_mm.to_dict(),
                "method": self.method,
                "ray_estimate_mm": self.ray_estimate_mm,
                "section_estimate_mm": self.section_estimate_mm,
                "section_count": self.section_count,
                "note": self.note,
                "assumptions": [self.note],
            },
            kind=ROUGH_ESTIMATE,
            name="Wall thickness",
            what=f"Minimum wall thickness is a rough estimate from {self.method} samples, not a measured caliper reading.",
            limits=[
                "A thin spot that no ray and no slice crosses is missed.",
                "A wall thinner than the smallest probe, 0.0004 mm, is an upper bound and cannot pass a minimum.",
                "A pass is evidence to read with the picture, not a guarantee the part will print.",
            ],
        )


@dataclass(frozen=True)
class ClearanceReport:
    part_a: str
    part_b: str
    intersecting: bool
    interference_volume_mm3: float
    min_distance_mm: float
    closest_point_a_mm: Vec3 | None
    closest_point_b_mm: Vec3 | None
    threshold_mm: float | None
    passed: bool | None
    note: str

    def to_dict(self) -> dict[str, object]:
        return finish(
            {
                "part_a": self.part_a,
                "part_b": self.part_b,
                "intersecting": self.intersecting,
                "interference_volume_mm3": self.interference_volume_mm3,
                "min_distance_mm": self.min_distance_mm,
                "closest_point_a_mm": None
                if self.closest_point_a_mm is None
                else self.closest_point_a_mm.to_dict(),
                "closest_point_b_mm": None
                if self.closest_point_b_mm is None
                else self.closest_point_b_mm.to_dict(),
                "threshold_mm": self.threshold_mm,
                "passed": self.passed,
                "note": self.note,
                "assumptions": [
                    "The interference volume is the boolean intersection of the two modeled solids.",
                    "The gap is the kernel distance between the surfaces. It is zero when the solids overlap.",
                ],
            },
            kind=VERIFIED_CHECK,
            name="Clearance",
            what="The gap or the overlap is a verified check of these two CAD solids.",
            limits=[
                "This is the model, not a manufactured clearance.",
                "The overlap volume is not a penetration depth.",
                "Catalog screws are simple shanks, without threads.",
                "The kernel's own tolerance applies.",
            ],
        )


@dataclass(frozen=True)
class PackagingPart:
    """One solid against the envelope."""

    name: str
    passed: bool
    contained: bool
    protrudes: bool
    min_clearance_mm: float
    clearance_at_mm: Vec3
    max_protrusion_mm: float
    protrusion_at_mm: Vec3 | None
    outside_volume_mm3: float
    method: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "contained": self.contained,
            "protrudes": self.protrudes,
            "min_clearance_mm": self.min_clearance_mm,
            "clearance_at_mm": self.clearance_at_mm.to_dict(),
            "max_protrusion_mm": self.max_protrusion_mm,
            "protrusion_at_mm": None if self.protrusion_at_mm is None else self.protrusion_at_mm.to_dict(),
            "outside_volume_mm3": self.outside_volume_mm3,
            "method": self.method,
        }


@dataclass(frozen=True)
class PackagingOverlap:
    """Material shared by two parts that also lies inside the envelope."""

    part_a: str
    part_b: str
    volume_mm3: float
    at_mm: Vec3

    def to_dict(self) -> dict[str, object]:
        return {
            "part_a": self.part_a,
            "part_b": self.part_b,
            "volume_mm3": self.volume_mm3,
            "at_mm": self.at_mm.to_dict(),
        }


@dataclass(frozen=True)
class PackagingReport:
    """Whether parts fit inside an envelope, and where they do not."""

    passed: bool
    required_clearance_mm: float
    min_clearance_mm: float
    min_clearance_part: str
    min_clearance_at_mm: Vec3
    envelope: dict
    parts: tuple[PackagingPart, ...]
    protruding: tuple[str, ...]
    overlaps: tuple[PackagingOverlap, ...]
    assembly: str | None
    pose: str | None
    joints: dict | None
    note: str

    def to_dict(self) -> dict[str, object]:
        return finish(
            {
                "passed": self.passed,
                "required_clearance_mm": self.required_clearance_mm,
                "min_clearance_mm": self.min_clearance_mm,
                "min_clearance_part": self.min_clearance_part,
                "min_clearance_at_mm": self.min_clearance_at_mm.to_dict(),
                "envelope": self.envelope,
                "parts": [part.to_dict() for part in self.parts],
                "protruding": list(self.protruding),
                "overlaps": [overlap.to_dict() for overlap in self.overlaps],
                "assembly": self.assembly,
                "pose": self.pose,
                "joints": self.joints,
                "note": self.note,
                "assumptions": [
                    "The envelope solid is the allowed volume. Its boundary is the wall.",
                    "min_clearance_mm is the smallest signed distance from a part to that wall.",
                    "A positive clearance is a gap inside the wall. Zero means the part touches the wall.",
                    "A negative clearance means the part sticks out, and its absolute value is max_protrusion_mm.",
                    "protrusion_at_mm is one point on the part where that protrusion is reached.",
                    "Overlap is the boolean intersection of two parts that also lies inside the envelope.",
                    "passed is false when any part is closer to the wall than required, or when any overlap is reported.",
                ],
            },
            kind=VERIFIED_CHECK,
            name="Packaging",
            what=(
                "Containment, wall clearance, protrusion, and part-to-part overlap "
                "are a verified check of these CAD solids against the envelope."
            ),
            limits=[
                "This is the model, not a manufactured fit.",
                "A polyhedron's protrusion is the farthest vertex outside the envelope.",
                "A curved surface is searched until the step is under 0.000001 mm.",
                "A face that sits on the wall within 0.0001 mm counts as touching, not as sticking out.",
                "The kernel's own tolerance applies.",
            ],
        )


@dataclass(frozen=True)
class RenderedView:
    name: str
    path: str
    width: int
    height: int

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "path": self.path, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class PartInfo:
    name: str
    source: str
    units: str
    volume_mm3: float
    valid: bool
    watertight: bool
    bounding_box: BoundingBox
    built: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "units": self.units,
            "volume_mm3": self.volume_mm3,
            "valid": self.valid,
            "watertight": self.watertight,
            "bounding_box": self.bounding_box.to_dict(),
            "built": self.built,
        }


@dataclass(frozen=True)
class FeatureStep:
    """How one boolean step changed the solid."""

    name: str
    op: str
    tool_volume_mm3: float
    result_volume_mm3: float
    new_material_mm3: float | None
    buried_mm3: float | None
    buried_fraction: float | None
    removed_mm3: float | None
    removed_fraction: float | None
    redundant: bool
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "op": self.op,
            "tool_volume_mm3": self.tool_volume_mm3,
            "result_volume_mm3": self.result_volume_mm3,
            "new_material_mm3": self.new_material_mm3,
            "buried_mm3": self.buried_mm3,
            "buried_fraction": self.buried_fraction,
            "removed_mm3": self.removed_mm3,
            "removed_fraction": self.removed_fraction,
            "redundant": self.redundant,
            "note": self.note,
        }


@dataclass(frozen=True)
class FeatureReport:
    tracked: bool
    steps: list[FeatureStep]
    redundant_features: list[str]
    warning: str | None
    note: str

    def to_dict(self) -> dict[str, object]:
        if self.tracked:
            what = "Each recorded boolean step reports how much material was new, already inside, or actually removed."
            limits = [
                "Only steps recorded with feature.add or feature.cut are in this list.",
                "The volumes are the model before and after that step, not a stress result.",
            ]
        else:
            what = "This part has no recorded boolean steps, so there is no contribution number."
            limits = [
                "A plain script is not a failed check. Call feature.add and feature.cut to record steps.",
            ]
        return finish(
            {
                "tracked": self.tracked,
                "steps": [step.to_dict() for step in self.steps],
                "redundant_features": list(self.redundant_features),
                "warning": self.warning,
                "note": self.note,
                "assumptions": [self.note],
            },
            kind=VERIFIED_CHECK,
            name="Feature contribution",
            what=what,
            limits=limits,
        )


def feature_report_from_dict(payload: dict | None) -> FeatureReport:
    """Rebuild a feature report that was stored next to a script part."""
    if not isinstance(payload, dict):
        return untracked_features()
    if not payload.get("tracked"):
        note = payload.get("note")
        if isinstance(note, str) and note:
            return FeatureReport(
                tracked=False,
                steps=[],
                redundant_features=[],
                warning=payload.get("warning") if isinstance(payload.get("warning"), str) else None,
                note=note,
            )
        return untracked_features()
    steps: list[FeatureStep] = []
    for row in payload.get("steps") or []:
        if not isinstance(row, dict):
            continue
        steps.append(
            FeatureStep(
                name=str(row.get("name") or ""),
                op=str(row.get("op") or ""),
                tool_volume_mm3=float(row.get("tool_volume_mm3") or 0),
                result_volume_mm3=float(row.get("result_volume_mm3") or 0),
                new_material_mm3=row.get("new_material_mm3"),
                buried_mm3=row.get("buried_mm3"),
                buried_fraction=row.get("buried_fraction"),
                removed_mm3=row.get("removed_mm3"),
                removed_fraction=row.get("removed_fraction"),
                redundant=bool(row.get("redundant")),
                note=str(row.get("note") or ""),
            )
        )
    redundant = payload.get("redundant_features") or [step.name for step in steps if step.redundant]
    return FeatureReport(
        tracked=True,
        steps=steps,
        redundant_features=[str(name) for name in redundant],
        warning=payload.get("warning") if isinstance(payload.get("warning"), str) else None,
        note=str(payload.get("note") or ""),
    )


def untracked_features() -> FeatureReport:
    return FeatureReport(
        tracked=False,
        steps=[],
        redundant_features=[],
        warning=None,
        note=(
            "This part was not built with feature.add or feature.cut, so contribution "
            "was not tracked. Plain build123d scripts still work. Call feature.add(name, solid) "
            "and feature.cut(name, tool) to record each boolean step."
        ),
    )


@dataclass(frozen=True)
class SectionLoop:
    """One closed curve of a cross-section, in the cutting plane's 2D coordinates."""

    kind: str
    points_mm: list[tuple[float, float]]

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "points_mm": [{"x": x, "y": y} for x, y in self.points_mm],
        }


@dataclass(frozen=True)
class SectionReport:
    area_mm2: float
    face_count: int
    plane_origin_mm: Vec3
    plane_normal: Vec3
    plane_x_mm: Vec3
    plane_y_mm: Vec3
    width_mm: float
    height_mm: float
    min_thickness_mm: float | None
    loops: list[SectionLoop]
    view: RenderedView | None
    note: str

    def to_dict(self) -> dict[str, object]:
        area = {
            "result_name": "Cut area",
            "result_kind": VERIFIED_CHECK,
            "result_label": "Verified check",
            "result_what": "The cut area and the outline size are the kernel's section of this plane.",
            "limits": ["The cut shows only this plane. Material between planes is not in the picture."],
            "assumptions": ["Loop coordinates are millimeters in the cutting plane."],
        }
        thickness = {
            "result_name": "Section thickness",
            "result_kind": ROUGH_ESTIMATE,
            "result_label": "Rough estimate",
            "result_what": "The minimum thickness is the narrowest width between roughly parallel edges in this one plane.",
            "limits": [
                "A neck that those edges do not catch is missed.",
                "A null thickness means no parallel pair was measured, not that the part is solid through.",
            ],
            "assumptions": [self.note],
        }
        sampled = self.face_count > 0
        return finish(
            {
                "area_mm2": self.area_mm2,
                "face_count": self.face_count,
                "plane_origin_mm": self.plane_origin_mm.to_dict(),
                "plane_normal": self.plane_normal.to_dict(),
                "plane_x_mm": self.plane_x_mm.to_dict(),
                "plane_y_mm": self.plane_y_mm.to_dict(),
                "width_mm": self.width_mm,
                "height_mm": self.height_mm,
                "min_thickness_mm": self.min_thickness_mm,
                "loops": [loop.to_dict() for loop in self.loops],
                "view": None if self.view is None else self.view.to_dict(),
                "note": self.note,
                "assumptions": [self.note],
            },
            kind=ROUGH_ESTIMATE if sampled else VERIFIED_CHECK,
            name="Section",
            what=(
                "The cut area is a verified check of this plane. The minimum thickness is a rough estimate from parallel edges."
                if sampled
                else "The plane misses the part. The area of zero is a verified check of that miss."
            ),
            limits=area["limits"] if not sampled else area["limits"] + thickness["limits"],
            results=[area, thickness] if sampled else [area],
        )


@dataclass(frozen=True)
class SketchReport:
    """What an assistant should read after solving a sketch."""

    name: str | None
    status: str
    dof: int
    solver: str
    free: list[dict[str, object]]
    conflicts: list[str]
    dimensions: list[dict[str, object]]
    geometry: dict[str, object]
    note: str
    view: RenderedView | None = None

    def to_dict(self) -> dict[str, object]:
        return finish(
            {
                "name": self.name,
                "status": self.status,
                "dof": self.dof,
                "solver": self.solver,
                "free": self.free,
                "conflicts": self.conflicts,
                "dimensions": self.dimensions,
                "geometry": self.geometry,
                "note": self.note,
                "view": None if self.view is None else self.view.to_dict(),
                "assumptions": [
                    "SolveSpace reports the degrees of freedom and the constraints that conflict.",
                    self.note,
                ],
            },
            kind=VERIFIED_CHECK,
            name="Sketch",
            what="Fully constrained, under-constrained, or over-constrained is a verified check of this sketch.",
            limits=[
                "A tolerance on a dimension is stored and printed. It does not move the geometry.",
                "The sketch lies on its plane: XY, YZ, XZ, an offset or angled datum, or a planar face. The initial guess has to be near a shape that satisfies the constraints.",
            ],
        )


@dataclass(frozen=True)
class FeaReport:
    """Linear static result an assistant can act on."""

    part: str | None
    status: str
    solver: str
    material: dict[str, object]
    mesh: dict[str, object]
    fixes: list[dict[str, object]]
    loads: list[dict[str, object]]
    max_von_mises_mpa: float | None
    max_von_mises_at_mm: Vec3 | None
    max_displacement_mm: float | None
    max_displacement_vector_mm: Vec3 | None
    max_displacement_at_mm: Vec3 | None
    min_safety_factor: float | None
    min_safety_factor_at_mm: Vec3 | None
    warnings: list[str]
    assumptions: list[str]
    review: dict[str, object] | None
    views: list[RenderedView]
    note: str
    probes: tuple | None = None

    def to_dict(self) -> dict[str, object]:
        def _point(value: Vec3 | None) -> dict[str, float] | None:
            return None if value is None else value.to_dict()

        degraded = bool((self.mesh or {}).get("degraded"))
        if self.status != "solved":
            what = "No stress was computed. " + self.note
        elif degraded:
            what = (
                "Linear static stress on a simplified or linear mesh. "
                "The safety factor is a rough estimate and is not a pass."
            )
        else:
            what = (
                "Linear static stress from a Gmsh mesh and a CalculiX solve. "
                "A rough estimate of this load on the CAD model, not a certified allowable."
            )
        limits = [
            "No plasticity, contact, buckling, or fatigue.",
            "A printed material is not given a weaker across-layer strength in the solve.",
            "A force is shared by the nodes on the selected faces. A bolt load is an axial force, not a thread.",
            "CalculiX runs as a separate program. It is not linked into aicad.",
        ]
        if degraded:
            limits.insert(
                0,
                "A coarse, linear, or defeatured mesh cannot be read as a pass even when the safety factor is high.",
            )
        return finish(
            {
                "part": self.part,
                "status": self.status,
                "solver": self.solver,
                "material": self.material,
                "mesh": self.mesh,
                "fixes": self.fixes,
                "loads": self.loads,
                "max_von_mises_mpa": self.max_von_mises_mpa,
                "max_von_mises_at_mm": _point(self.max_von_mises_at_mm),
                "max_displacement_mm": self.max_displacement_mm,
                "max_displacement_vector_mm": _point(self.max_displacement_vector_mm),
                "max_displacement_at_mm": _point(self.max_displacement_at_mm),
                "min_safety_factor": self.min_safety_factor,
                "min_safety_factor_at_mm": _point(self.min_safety_factor_at_mm),
                "warnings": self.warnings,
                "assumptions": self.assumptions,
                "review": self.review,
                "views": [view.to_dict() for view in self.views],
                "note": self.note,
                **({} if self.probes is None else {"probes": list(self.probes)}),
            },
            kind=ROUGH_ESTIMATE,
            name="Strength",
            what=what,
            limits=limits,
        )


@dataclass(frozen=True)
class InspectReport:
    part: PartInfo
    measurement: Measurement
    wall: WallReport
    features: FeatureReport
    views: list[RenderedView]
    sketch: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        measurement = self.measurement.to_dict()
        wall = self.wall.to_dict()
        features = self.features.to_dict()
        blocks = [slim(measurement, "Model measurement"), slim(wall, "Wall thickness"), slim(features, "Feature contribution")]
        payload = {
            "part": self.part.to_dict(),
            "measurement": measurement,
            "wall": wall,
            "features": features,
            "views": [view.to_dict() for view in self.views],
            "assumptions": ["Read the label on the measurement, the wall estimate, and the feature report. They are not the same kind of result."],
        }
        if self.sketch is not None:
            payload["sketch"] = self.sketch
            if isinstance(self.sketch, dict) and self.sketch.get("result_label"):
                blocks.append(slim(self.sketch, "Sketch"))
        return finish(
            payload,
            kind=weakest([block.get("result_kind") or "" for block in blocks]),
            name="Inspect",
            what="Inspection mixes a verified model check with a rough wall-thickness estimate. Read each label.",
            limits=["A pass on the wall estimate does not verify the strength, the mass, or the print."],
            results=blocks,
        )
