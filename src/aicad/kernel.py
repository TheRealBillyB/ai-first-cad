"""Geometry kernel adapter.

build123d and OpenCascade are imported here. ``aicad.library`` imports
bd_warehouse, and ``aicad.sketch`` imports the SolveSpace solver. The rest of
aicad talks to plain records and to :class:`Shape`.
"""

from __future__ import annotations

import io
import math
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from build123d import (
    Axis,
    Box,
    Cylinder,
    Face,
    Plane,
    Polygon,
    Pos,
    Rot,
    Vector,
    export_step,
    export_stl,
    extrude,
    import_step,
    revolve,
)
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

from aicad.errors import GeometryError, ScriptError
from aicad.refs import edge_ref, face_ref
from aicad.models import (
    BoundingBox,
    FeatureReport,
    FeatureStep,
    Measurement,
    SectionLoop,
    Vec3,
    WallEstimate,
    round_mm,
    untracked_features,
)

UNITS = "mm"
_INTERFERENCE_VOLUME = 1e-4
_NAME_IN_SCRIPT = "part"


@dataclass
class Shape:
    """An opaque solid (or a compound of solids) owned by the kernel."""

    _impl: object
    features: FeatureReport | None = None

    @property
    def impl(self):
        return self._impl

    def feature_report(self) -> FeatureReport:
        return self.features if self.features is not None else untracked_features()


@dataclass
class TriangleMesh:
    """Tessellation used by the software renderer. Arrays are kernel-private."""

    vertices: np.ndarray
    normals: np.ndarray
    triangles: np.ndarray
    edges: list[np.ndarray]
    colors: np.ndarray | None = None


def make_from_script(code: str) -> Shape:
    """Execute a build123d script and return the solid assigned to ``part``."""
    if not isinstance(code, str) or not code.strip():
        raise ScriptError("The script is empty. Assign the finished solid to `part`.")
    if len(code) > 200_000:
        raise ScriptError("The script is longer than 200000 characters.")

    log = FeatureLog()
    namespace: dict[str, object] = {
        "__builtins__": __builtins__,
        "__name__": "aicad_script",
        "feature": log,
    }
    try:
        # Discard prints. A script that writes to stdout would break an MCP stdio session.
        with redirect_stdout(io.StringIO()):
            exec("from build123d import *\nimport math", namespace)  # noqa: S102
            compiled = compile(code, "<aicad-script>", "exec")
            exec(compiled, namespace)  # noqa: S102
    except ScriptError:
        raise
    except Exception as exc:
        raise ScriptError(_format_script_error(code, exc)) from exc

    if _NAME_IN_SCRIPT not in namespace:
        raise ScriptError(
            "The script did not assign `part`. Build a solid and end with `part = ...`."
        )
    try:
        solid = _require_solid(namespace[_NAME_IN_SCRIPT])
    except GeometryError as exc:
        raise ScriptError(str(exc)) from exc
    # A script may replace `feature` with its own FeatureLog (the examples do this
    # so they can also run outside the document). Prefer that log when it has steps.
    active = namespace.get("feature", log)
    if isinstance(active, FeatureLog):
        log = active
    report = log.report()
    if report.tracked and log.current is not None:
        logged = float(log.current.volume)
        final = float(solid.volume)
        if abs(logged - final) > max(1e-2, 0.02 * max(final, 1.0)):
            report = FeatureReport(
                tracked=True,
                steps=report.steps,
                redundant_features=report.redundant_features,
                warning=(
                    "`part` does not match the feature log result, so the contribution "
                    "describes the feature steps rather than the solid that was stored."
                ),
                note=report.note,
            )
    return Shape(solid, features=report)


def make_from_ops(ops: list[dict]) -> Shape:
    """Build a solid from a short list of add/cut box and cylinder operations."""
    if not isinstance(ops, list) or not ops:
        raise GeometryError("ops must be a non-empty list of operations.")
    if len(ops) > 500:
        raise GeometryError("A part may have at most 500 operations.")

    log = FeatureLog()
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            raise GeometryError(f"Operation {index} must be an object.")
        kind = op.get("op")
        if kind not in ("add", "cut"):
            raise GeometryError(
                f"Operation {index} has op {kind!r}. Use 'add' or 'cut'."
            )
        tool = _primitive(op, index)
        name = op.get("name") or f"{kind}_{index + 1}"
        if not isinstance(name, str) or not name.strip():
            raise GeometryError(f"Operation {index} name must be a non-empty string.")
        if kind == "add":
            log.add(name, tool)
        else:
            if log.current is None:
                raise GeometryError(
                    f"Operation {index} is a cut, but the part has no solid yet. Add one first."
                )
            log.cut(name, tool)
            if _volume(log.current) <= 0 or len(log.current.solids()) == 0:
                raise GeometryError(f"Operation {index} (cut) removed the entire part.")
    return Shape(_require_solid(log.current), features=log.report())


def measure(shape: Shape) -> Measurement:
    impl = shape.impl
    bbox = impl.bounding_box()
    low = bbox.min
    high = bbox.max
    size = bbox.size
    center = impl.center()
    return Measurement(
        units=UNITS,
        valid=bool(impl.is_valid),
        watertight=_is_watertight(impl),
        solid_count=len(impl.solids()),
        face_count=len(impl.faces()),
        edge_count=len(impl.edges()),
        vertex_count=len(impl.vertices()),
        volume_mm3=round_mm(impl.volume),
        area_mm2=round_mm(impl.area),
        center_of_mass_mm=_vec(center),
        bounding_box=BoundingBox(
            min=_vec(low),
            max=_vec(high),
            size=_vec(size),
        ),
    )


def estimate_wall_thickness(shape: Shape, samples_per_edge: int = 4) -> WallEstimate:
    """Estimate minimum wall thickness from rays and from planar sections.

    The reported minimum is the smaller of the two. ``method`` says which one
    produced it, or ``ray+section`` when they agree within 0.05 mm.
    """
    ray_mm, accepted, used, point = _ray_wall_thickness(shape, samples_per_edge)
    section_mm, section_count = _section_wall_thickness(shape)
    candidates = [value for value in (ray_mm, section_mm) if value is not None]
    minimum = min(candidates) if candidates else None
    if ray_mm is None and section_mm is None:
        method = "none"
    elif ray_mm is None:
        method = "section"
    elif section_mm is None:
        method = "ray"
    elif abs(ray_mm - section_mm) <= 0.05:
        method = "ray+section"
    elif section_mm < ray_mm:
        method = "section"
    else:
        method = "ray"
    if method == "section":
        point = None
    note = (
        f"Minimum came from {method}. "
        f"Ray samples: {_mm(ray_mm)} mm. "
        f"Planar sections ({section_count} slices): {_mm(section_mm)} mm. "
        "Rays shoot inward from a grid on each face and keep hits on a roughly "
        "parallel face. Sections slice the part at 25%, 50%, and 75% of its "
        "extent along X, Y, and Z and measure the width between roughly parallel "
        "edges in that plane. The smaller number is the estimate. Either method "
        "can miss a thin spot it does not cross."
    )
    return WallEstimate(
        minimum_mm=None if minimum is None else round_mm(minimum),
        ray_mm=None if ray_mm is None else round_mm(ray_mm),
        section_mm=None if section_mm is None else round_mm(section_mm),
        method=method,
        sample_count=accepted,
        samples_per_edge=used,
        section_count=section_count,
        thinnest_point_mm=point,
        note=note,
    )


def _ray_wall_thickness(
    shape: Shape, samples_per_edge: int
) -> tuple[float | None, int, int, Vec3 | None]:
    impl = shape.impl
    face_count = len(impl.faces())
    n = samples_per_edge
    while n > 1 and face_count * n * n > 8000:
        n -= 1

    epsilon = 0.05
    thinnest: float | None = None
    thinnest_point: Vec3 | None = None
    accepted = 0

    for face in impl.faces():
        for i in range(n):
            for j in range(n):
                u = (i + 0.5) / n
                v = (j + 0.5) / n
                try:
                    point = face.position_at(u, v)
                    normal = face.normal_at(u, v)
                except Exception:
                    continue
                if normal.length < 1e-9 or not face.is_inside(point):
                    continue
                inward = _inward_normal(impl, point, normal, epsilon)
                if inward is None:
                    continue
                origin = point + inward * epsilon
                distance = _first_parallel_hit(impl, origin, inward)
                if distance is None:
                    continue
                thickness = distance + epsilon
                accepted += 1
                if thinnest is None or thickness < thinnest:
                    thinnest = thickness
                    thinnest_point = _vec(point)
    return thinnest, accepted, n, thinnest_point


def clearance_between(a: Shape, b: Shape) -> tuple[float, float, Vec3 | None, Vec3 | None]:
    """Return intersection volume, surface gap, and the closest point on each solid.

    The gap is zero when the solids interfere. It is not a penetration depth.
    """
    common = a.impl & b.impl
    overlap = float(common.volume) if common is not None else 0.0
    if overlap < 0:
        overlap = 0.0
    distance, first, second = a.impl.distance_to_with_closest_points(b.impl)
    if overlap > _INTERFERENCE_VOLUME:
        distance = 0.0
    return round_mm(overlap), round_mm(distance), _vec(first), _vec(second)


def export_step_file(shape: Shape, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not export_step(shape.impl, path):
        raise GeometryError(f"Could not write STEP file {path}.")


def export_stl_file(
    shape: Shape,
    path: Path,
    linear_tolerance: float = 0.05,
    angular_tolerance: float = 0.2,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not export_stl(
        shape.impl,
        path,
        tolerance=linear_tolerance,
        angular_tolerance=angular_tolerance,
    ):
        raise GeometryError(f"Could not write STL file {path}.")


def import_step_file(path: Path) -> Shape:
    if not path.is_file():
        raise GeometryError(f"STEP file not found: {path}")
    try:
        imported = import_step(path)
    except Exception as exc:
        raise GeometryError(f"Could not read STEP file {path}: {exc}") from exc
    return Shape(_require_solid(imported))


def tessellate(shape: Shape) -> TriangleMesh:
    """Mesh the solid and discretize its edges for rendering."""
    impl = shape.impl
    diagonal = float(impl.bounding_box().diagonal)
    linear = min(0.12, max(0.04, diagonal * 0.0015))
    vertices, triangles, normals, _uvs = impl.tessellate_with_uvs(
        linear, angular_tolerance=0.18
    )
    if not triangles:
        raise GeometryError("The solid produced an empty mesh and cannot be rendered.")

    points = np.array([(v.X, v.Y, v.Z) for v in vertices], dtype=np.float64)
    norms = np.array([(v.X, v.Y, v.Z) for v in normals], dtype=np.float64)
    faces = np.array(triangles, dtype=np.int32)
    deflection = min(0.2, max(0.04, diagonal * 0.0015))
    edges: list[np.ndarray] = []
    for edge in impl.edges():
        samples = edge.positions(deflection=deflection)
        if len(samples) >= 2:
            edges.append(np.array([(p.X, p.Y, p.Z) for p in samples], dtype=np.float64))
    return TriangleMesh(vertices=points, normals=norms, triangles=faces, edges=edges)


def describe_faces(shape: Shape) -> list[dict]:
    """Describe each face so a load or a support can name it without a click.

    Planes carry an outward normal. Cylinders carry a radius and an axis. The
    axis is flipped so its largest component is positive, which makes a vertical
    hole point +Z.
    """
    catalog = []
    for index, face in enumerate(shape.impl.faces()):
        center = face.center()
        kind = "other"
        normal = None
        radius = None
        axis_origin = None
        axis_direction = None
        adaptor = BRepAdaptor_Surface(face.wrapped)
        surface_type = adaptor.GetType()
        if surface_type == GeomAbs_SurfaceType.GeomAbs_Plane:
            kind = "plane"
            normal = _outward_normal(face, center)
        elif surface_type == GeomAbs_SurfaceType.GeomAbs_Cylinder:
            kind = "cylinder"
            cylinder = adaptor.Cylinder()
            radius = float(cylinder.Radius())
            axis = cylinder.Axis()
            origin = axis.Location()
            direction = axis.Direction()
            axis_origin = [float(origin.X()), float(origin.Y()), float(origin.Z())]
            axis_direction = _flip_axis(_unit3(direction.X(), direction.Y(), direction.Z()))
            normal = _outward_normal(face, center)
        catalog.append(
            {
                "index": index,
                "kind": kind,
                "area_mm2": round_mm(float(face.area)),
                "center_mm": [round_mm(center.X), round_mm(center.Y), round_mm(center.Z)],
                "normal": normal,
                "radius_mm": None if radius is None else round_mm(radius),
                "axis_origin_mm": None if axis_origin is None else [round_mm(v) for v in axis_origin],
                "axis_direction": None if axis_direction is None else [round_mm(v, 6) for v in axis_direction],
            }
        )
        catalog[-1]["ref"] = face_ref(catalog[-1])
    return catalog


def describe_edges(shape: Shape) -> list[dict]:
    """Describe each edge so a click and an assistant share one reference."""
    catalog = []
    for index, edge in enumerate(shape.impl.edges()):
        kind = str(edge.geom_type).split(".")[-1].lower()
        if kind not in ("line", "circle", "ellipse", "bspline", "bezier"):
            kind = "curve"
        samples = edge.positions(deflection=0.4)
        if len(samples) >= 2:
            midpoint = [
                round_mm(float(samples[len(samples) // 2].X)),
                round_mm(float(samples[len(samples) // 2].Y)),
                round_mm(float(samples[len(samples) // 2].Z)),
            ]
        else:
            center = edge.center()
            midpoint = [round_mm(center.X), round_mm(center.Y), round_mm(center.Z)]
        record = {
            "index": index,
            "kind": "circle" if kind == "circle" else "line" if kind == "line" else "curve",
            "length_mm": round_mm(float(edge.length)),
            "midpoint_mm": midpoint,
        }
        record["ref"] = edge_ref(record["kind"], midpoint)
        catalog.append(record)
    return catalog


def viewport_mesh(shape: Shape) -> dict:
    """Triangles tagged by face, plus edge polylines, for the workspace viewer.

    A face reference on each triangle is the same string ``describe_faces``
    publishes, so a click and an assistant name the same face.
    """
    faces = describe_faces(shape)
    edges = describe_edges(shape)
    impl = shape.impl
    diagonal = float(impl.bounding_box().diagonal) or 1.0
    tolerance = min(0.28, max(0.08, diagonal * 0.004))
    positions: list[float] = []
    normals: list[float] = []
    triangle_face: list[int] = []
    for index, face in enumerate(impl.faces()):
        vertices, triangles = face.tessellate(tolerance)
        if not triangles:
            continue
        points = [(float(vertex.X), float(vertex.Y), float(vertex.Z)) for vertex in vertices]
        for tri in triangles:
            corners = [points[int(tri[0])], points[int(tri[1])], points[int(tri[2])]]
            ax, ay, az = corners[1][0] - corners[0][0], corners[1][1] - corners[0][1], corners[1][2] - corners[0][2]
            bx, by, bz = corners[2][0] - corners[0][0], corners[2][1] - corners[0][1], corners[2][2] - corners[0][2]
            nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            nx, ny, nz = nx / length, ny / length, nz / length
            for corner in corners:
                positions.extend(round(component, 4) for component in corner)
                normals.extend((round(nx, 4), round(ny, 4), round(nz, 4)))
            triangle_face.append(index)
    edge_payload = []
    for edge, record in zip(impl.edges(), edges):
        samples = edge.positions(deflection=tolerance)
        edge_payload.append(
            {
                "index": record["index"],
                "kind": record["kind"],
                "ref": record["ref"],
                "length_mm": record["length_mm"],
                "midpoint_mm": record["midpoint_mm"],
                "points": [[round(float(point.X), 4), round(float(point.Y), 4), round(float(point.Z), 4)] for point in samples],
            }
        )
    bbox = impl.bounding_box()
    return {
        "units": "mm",
        "positions": positions,
        "normals": normals,
        "triangle_face": triangle_face,
        "faces": faces,
        "edges": edge_payload,
        "bounding_box": {
            "min": [round_mm(bbox.min.X), round_mm(bbox.min.Y), round_mm(bbox.min.Z)],
            "max": [round_mm(bbox.max.X), round_mm(bbox.max.Y), round_mm(bbox.max.Z)],
        },
    }


def distance_point_to_face(shape: Shape, face_index: int, point: tuple[float, float, float]) -> float:
    """Shortest distance from a point to a face, in millimeters."""
    faces = list(shape.impl.faces())
    if face_index < 0 or face_index >= len(faces):
        raise GeometryError(f"Face index {face_index} is outside this part.")
    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(float(point[0]), float(point[1]), float(point[2]))).Shape()
    extrema = BRepExtrema_DistShapeShape(faces[face_index].wrapped, vertex)
    extrema.Perform()
    return float(extrema.Value())


def _outward_normal(face, center) -> list[float] | None:
    try:
        normal = face.normal_at(center)
    except Exception:
        return None
    return _unit3(normal.X, normal.Y, normal.Z)


def _unit3(x: float, y: float, z: float) -> list[float]:
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12:
        return [0.0, 0.0, 0.0]
    return [x / length, y / length, z / length]


def _flip_axis(direction: list[float]) -> list[float]:
    dominant = max(range(3), key=lambda index: abs(direction[index]))
    if direction[dominant] < 0:
        return [-component for component in direction]
    return direction


def _require_solid(obj: object):
    if type(obj).__name__ == "BuildPart" and hasattr(obj, "part"):
        obj = obj.part
    if not hasattr(obj, "solids") or not hasattr(obj, "volume"):
        raise GeometryError(
            "Expected a 3D solid assigned to `part`. A sketch, curve, or other value has no volume."
        )
    if len(obj.solids()) == 0 or float(obj.volume) <= 0:
        raise GeometryError(
            "The result has no solid volume. Assign a closed 3D solid to `part` "
            "(combine bodies with `part = a + b`)."
        )
    return obj


def _volume(obj) -> float:
    try:
        return float(obj.volume)
    except Exception:
        return 0.0


def _is_watertight(impl) -> bool:
    """A valid manifold solid with volume. ``is_manifold`` is build123d's closed-shell check."""
    try:
        return bool(
            len(impl.solids()) >= 1
            and impl.volume > 0
            and impl.is_valid
            and impl.is_manifold
        )
    except Exception:
        return False


def _vec(vector) -> Vec3:
    return Vec3(round_mm(vector.X), round_mm(vector.Y), round_mm(vector.Z))


def _format_script_error(code: str, exc: BaseException) -> str:
    import traceback

    line_no = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == "<aicad-script>":
            line_no = frame.lineno
    lines = code.splitlines()
    snippet = ""
    if line_no is not None and 1 <= line_no <= len(lines):
        snippet = lines[line_no - 1].strip()
    where = f" on line {line_no}" if line_no is not None else ""
    message = f"Script failed{where}: {type(exc).__name__}: {exc}"
    if snippet:
        message += f"\n    {snippet}"
    return message


def _primitive(op: dict, index: int):
    kind = op.get("shape")
    center = _vec3_field(op.get("center", [0, 0, 0]), f"operation {index} center")
    if kind == "box":
        if "axis" in op:
            raise GeometryError(
                f"Operation {index}: box is axis-aligned. Use a cylinder to pick an axis, "
                "or place a rotated box in a script."
            )
        size = _vec3_field(op.get("size"), f"operation {index} size")
        if any(component <= 0 for component in size):
            raise GeometryError(f"Operation {index}: box size must be positive.")
        solid = Box(size[0], size[1], size[2])
        if center != (0.0, 0.0, 0.0):
            solid = Pos(*center) * solid
        return solid
    if kind == "cylinder":
        radius = _positive(op.get("radius"), f"operation {index} radius")
        height = _positive(op.get("height"), f"operation {index} height")
        axis = _vec3_field(op.get("axis", [0, 0, 1]), f"operation {index} axis")
        length = math.sqrt(sum(component * component for component in axis))
        if length < 1e-9:
            raise GeometryError(f"Operation {index}: cylinder axis must be non-zero.")
        direction = tuple(component / length for component in axis)
        solid = Plane(origin=center, z_dir=direction) * Cylinder(radius, height)
        return solid
    raise GeometryError(
        f"Operation {index}: unknown shape {kind!r}. Use 'box' or 'cylinder'."
    )


def _vec3_field(value, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError(f"{label} must be a list of three numbers.")
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a list of three numbers.") from exc


def _positive(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a positive number.") from exc
    if number <= 0:
        raise GeometryError(f"{label} must be a positive number.")
    return number


def _inward_normal(impl, point, normal, epsilon):
    normal = normal.normalized()
    into_negative = impl.is_inside(point - normal * epsilon) and not impl.is_inside(
        point + normal * epsilon
    )
    into_positive = impl.is_inside(point + normal * epsilon) and not impl.is_inside(
        point - normal * epsilon
    )
    if into_negative:
        return -normal
    if into_positive:
        return normal
    return None


def _first_parallel_hit(impl, origin, direction) -> float | None:
    """Distance along ``direction`` to the first opposing face, or None."""
    line = gp_Lin(
        gp_Pnt(origin.X, origin.Y, origin.Z),
        gp_Dir(direction.X, direction.Y, direction.Z),
    )
    intersector = BRepIntCurveSurface_Inter()
    intersector.Init(impl.wrapped, line, 1e-6)
    best: float | None = None
    while intersector.More():
        parameter = float(intersector.W())
        if parameter > 0.02:
            hit = Face(intersector.Face())
            try:
                hit_normal = hit.normal_at(Vector(intersector.Pnt()))
                aligned = abs(hit_normal.normalized().dot(direction))
            except Exception:
                aligned = 0.0
            if aligned > 0.7 and (best is None or parameter < best):
                best = parameter
        intersector.Next()
    return best


_BURIED_FLAG = 0.5
_VOLUME_NOISE = 1e-4
_SECTION_FRACTIONS = (0.25, 0.5, 0.75)


class FeatureLog:
    """Opt-in boolean history for a script.

    ``feature.add(name, solid)`` and ``feature.cut(name, tool)`` fuse or cut
    and remember how much material the step actually added or removed. Scripts
    that never call them are unchanged.
    """

    def __init__(self) -> None:
        self.steps: list[FeatureStep] = []
        self.current = None
        self.tracked = False

    def add(self, name: str, tool):
        return self._apply("add", name, tool)

    def cut(self, name: str, tool):
        return self._apply("cut", name, tool)

    @property
    def result(self):
        if self.current is None:
            raise GeometryError("feature has no solids yet. Call feature.add first.")
        return self.current

    def report(self) -> FeatureReport:
        if not self.tracked:
            return untracked_features()
        return FeatureReport(
            tracked=True,
            steps=list(self.steps),
            redundant_features=[step.name for step in self.steps if step.redundant],
            warning=None,
            note=(
                "An added feature is flagged when at least half of its volume was already "
                "inside the part. A cut is flagged when it removes nothing. A through hole "
                "whose cutter sticks out of the part is not flagged; read removed_fraction."
            ),
        )

    def _apply(self, op: str, name: str, tool):
        if not isinstance(name, str) or not name.strip():
            raise GeometryError("A feature name must be a non-empty string.")
        tool = _require_solid(tool)
        tool_volume = float(tool.volume)
        if tool_volume <= _VOLUME_NOISE:
            raise GeometryError(f"Feature {name!r} has no volume.")
        before = self.current
        before_volume = 0.0 if before is None else float(before.volume)
        if op == "add":
            after = tool if before is None else before + tool
            after_volume = float(after.volume)
            new_material = max(0.0, after_volume - before_volume)
            if new_material > tool_volume:
                new_material = tool_volume
            buried = max(0.0, tool_volume - new_material)
            fraction = buried / tool_volume
            redundant = fraction + 1e-9 >= _BURIED_FLAG
            if redundant:
                note = (
                    f"{fraction:.0%} of '{name}' was already inside the part "
                    f"({buried:.1f} of {tool_volume:.1f} mm^3). It adds little new material. "
                    "A rib or gusset buried in a boss does not stiffen the joint."
                )
            else:
                note = (
                    f"{1 - fraction:.0%} of '{name}' became new material "
                    f"({new_material:.1f} of {tool_volume:.1f} mm^3)."
                )
            step = FeatureStep(
                name=name,
                op="add",
                tool_volume_mm3=round_mm(tool_volume),
                result_volume_mm3=round_mm(after_volume),
                new_material_mm3=round_mm(new_material),
                buried_mm3=round_mm(buried),
                buried_fraction=round_mm(fraction, 4),
                removed_mm3=None,
                removed_fraction=None,
                redundant=redundant,
                note=note,
            )
        else:
            if before is None:
                raise GeometryError(
                    f"feature.cut({name!r}) has nothing to cut. Call feature.add first."
                )
            after = before - tool
            after_volume = float(after.volume)
            if after_volume <= _VOLUME_NOISE or len(after.solids()) == 0:
                raise GeometryError(f"feature.cut({name!r}) removed the entire part.")
            removed = max(0.0, before_volume - after_volume)
            fraction = removed / tool_volume
            redundant = removed <= 1e-3
            if redundant:
                note = f"'{name}' removed no material. The cutter missed the part."
            else:
                note = (
                    f"'{name}' removed {removed:.1f} mm^3 ({fraction:.0%} of the cutter). "
                    "A through hole is often a small fraction of a long cutter; that is expected."
                )
            step = FeatureStep(
                name=name,
                op="cut",
                tool_volume_mm3=round_mm(tool_volume),
                result_volume_mm3=round_mm(after_volume),
                new_material_mm3=None,
                buried_mm3=None,
                buried_fraction=None,
                removed_mm3=round_mm(removed),
                removed_fraction=round_mm(fraction, 4),
                redundant=redundant,
                note=note,
            )
        self.current = after
        self.steps.append(step)
        self.tracked = True
        return after


def adopt(impl) -> Shape:
    """Wrap a solid built by the parts library."""
    return Shape(_require_solid(impl), features=untracked_features())


def place(shape: Shape, position: tuple[float, float, float], rotation_deg: tuple[float, float, float]) -> Shape:
    """Move a solid. Rotation is degrees about X, then Y, then Z, then the translation."""
    from build123d import Pos, Rot

    px, py, pz = position
    rx, ry, rz = rotation_deg
    if all(abs(value) < 1e-12 for value in (px, py, pz, rx, ry, rz)):
        return shape
    moved = Pos(px, py, pz) * Rot(rx, ry, rz) * shape.impl
    return Shape(_require_solid(moved), features=shape.feature_report())


def solid_from_sketch(geometry: dict, steps: list[dict], feature: FeatureLog | None = None) -> Shape:
    """Extrude, revolve, or cut a solved sketch into a solid.

    ``geometry`` is the ``geometry`` dict from a sketch report. ``steps`` is a
    list of operations. Each one is recorded on ``feature`` when that log is
    passed in (scripts already have one); otherwise a new log is used.

    An extrude or a cut reads coordinates from the sketch, so solving again
    with a new driving dimension and calling this again rebuilds the solid.
    ``cut_box`` and ``gusset`` are ordinary 3D recipes: their numbers are not
    sketch dimensions.

    Operations:

    - ``{"op": "extrude", "points": ["a", "b", "c"], "height": 4, "z": 0, "name": "base"}``
    - ``{"op": "extrude", "circle": "boss", "height": 8, "z": 4, "name": "boss"}``
    - ``{"op": "cut", "circle": "bore", "height": 14, "name": "bore"}`` (or ``points``)
    - ``{"op": "revolve", "points": ["a", "b", "c"], "axis": "y", "angle": 360, "name": "knob"}``
    - ``{"op": "cut_box", "center": [0, 26, 2], "size": [10, 8, 6], "name": "slot"}``
    - ``{"op": "gusset", "angle": 0, "outer": 30, "plate_thickness": 4, "boss_height": 8, "name": "gusset_0"}``

    ``z`` is the bottom of an extrusion or a cutter. A revolve spins the XY
    profile about the sketch X or Y axis. The profile must stay on one side of
    that axis.
    """
    if not isinstance(geometry, dict) or not isinstance(geometry.get("points"), dict):
        raise GeometryError(
            "solid_from_sketch needs solved sketch geometry, with points, lines, circles, and arcs."
        )
    if not isinstance(steps, list) or not steps:
        raise GeometryError(
            "solid_from_sketch needs steps: extrude, cut, revolve, cut_box, or gusset."
        )
    if len(steps) > 500:
        raise GeometryError("A part may have at most 500 sketch steps.")
    log = feature if isinstance(feature, FeatureLog) else FeatureLog()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise GeometryError(f"Sketch step {index} must be an object.")
        op = step.get("op")
        name = step.get("name") or f"{op}_{index + 1}"
        if not isinstance(name, str) or not name.strip():
            raise GeometryError(f"Sketch step {index} name must be a non-empty string.")
        if op in ("extrude", "revolve", "gusset"):
            tool = _sketch_tool(step, geometry, index, name)
            log.add(name, tool)
        elif op in ("cut", "cut_box"):
            if log.current is None:
                raise GeometryError(
                    f"Sketch step {index} ({name}) is a cut, but nothing has been added yet."
                )
            tool = _sketch_tool(step, geometry, index, name)
            log.cut(name, tool)
        else:
            raise GeometryError(
                f"Sketch step {index} has op {op!r}. "
                "Use extrude, cut, revolve, cut_box, or gusset."
            )
    return Shape(_require_solid(log.current), features=log.report())


def _sketch_tool(step: dict, geometry: dict, index: int, name: str):
    op = step["op"]
    if op == "cut_box":
        center = _vec3_field(step.get("center", [0, 0, 0]), f"{name} center")
        size = _vec3_field(step.get("size"), f"{name} size")
        if any(component <= 0 for component in size):
            raise GeometryError(f"{name}: box size must be positive.")
        return Pos(*center) * Box(*size)
    if op == "gusset":
        return _radial_gusset(step, name)
    if op == "revolve":
        return _revolve_profile(step, geometry, name)
    if "circle" in step:
        return _circle_prism(step, geometry, name)
    if "points" in step:
        return _polygon_prism(step, geometry, name)
    raise GeometryError(
        f"{name}: an {op} needs `circle` or `points` from the solved sketch."
    )


def _circle_prism(step: dict, geometry: dict, name: str):
    circle_id = step.get("circle")
    circles = geometry.get("circles") or {}
    if not isinstance(circle_id, str) or circle_id not in circles:
        known = ", ".join(sorted(circles)) or "(none)"
        raise GeometryError(f"{name}: no circle {circle_id!r}. Circles: {known}.")
    circle = circles[circle_id]
    radius = float(circle["radius"])
    if radius <= 0:
        raise GeometryError(f"{name}: circle {circle_id!r} has no radius.")
    height = _positive(step.get("height"), f"{name} height")
    z = _optional_number(step.get("z", 0.0), f"{name} z")
    center = circle["center"]
    return Pos(float(center["x"]), float(center["y"]), z + height / 2) * Cylinder(radius, height)


def _polygon_prism(step: dict, geometry: dict, name: str):
    coords = _profile_points(step, geometry, name)
    height = _positive(step.get("height"), f"{name} height")
    z = _optional_number(step.get("z", 0.0), f"{name} z")
    try:
        face = Plane.XY * Polygon(*coords)
        solid = Pos(0, 0, z) * extrude(face, amount=height)
    except GeometryError:
        raise
    except Exception as exc:
        raise GeometryError(
            f"{name}: could not extrude that profile. The points may be colinear "
            f"or self-intersecting. {exc}"
        ) from exc
    return solid


def _revolve_profile(step: dict, geometry: dict, name: str):
    coords = _profile_points(step, geometry, name)
    axis_name = str(step.get("axis", "y")).lower()
    if axis_name not in ("x", "y"):
        raise GeometryError(f"{name}: revolve axis must be 'x' or 'y'.")
    angle = _positive(step.get("angle", 360.0), f"{name} angle")
    if angle > 360:
        raise GeometryError(f"{name}: revolve angle must be from 0 to 360 degrees.")
    radial = 0 if axis_name == "y" else 1
    signs = [coord[radial] for coord in coords]
    if min(signs) < -1e-9 and max(signs) > 1e-9:
        raise GeometryError(
            f"{name}: the profile crosses the {axis_name.upper()} axis. "
            "Keep every point on one side of the axis it revolves around."
        )
    axis = Axis.Y if axis_name == "y" else Axis.X
    try:
        solid = revolve(Plane.XY * Polygon(*coords), axis=axis, revolution_arc=angle)
    except Exception as exc:
        raise GeometryError(f"{name}: revolve failed. {exc}") from exc
    return solid


def _profile_points(step: dict, geometry: dict, name: str) -> list[tuple[float, float]]:
    ids = step.get("points")
    points = geometry.get("points") or {}
    if not isinstance(ids, list) or len(ids) < 3:
        raise GeometryError(f"{name}: points must be three or more sketch point ids, in order.")
    coords = []
    for point_id in ids:
        if not isinstance(point_id, str) or point_id not in points:
            known = ", ".join(sorted(points)) or "(none)"
            raise GeometryError(f"{name}: no point {point_id!r}. Points: {known}.")
        point = points[point_id]
        coords.append((float(point["x"]), float(point["y"])))
    return coords


def _radial_gusset(step: dict, name: str):
    """The same blunt radial gusset the motor-mount example uses.

    A triangle that dies inside the cylindrical boss is non-manifold, and a
    knife edge is not a wall. This is a block that bites the boss wall, fused
    to a trapezoid that stays outside the cylinder.
    """
    angle = _optional_number(step.get("angle"), f"{name} angle")
    outer = _positive(step.get("outer"), f"{name} outer")
    plate_t = _positive(step.get("plate_thickness"), f"{name} plate_thickness")
    boss_h = _positive(step.get("boss_height"), f"{name} boss_height")
    thick = _positive(step.get("thick", 2.6), f"{name} thick")
    toe = _positive(step.get("toe", 2.4), f"{name} toe")
    bite = Pos(17.2, 0, plate_t + boss_h / 2) * Box(3.2, thick, boss_h)
    fin = extrude(
        Plane.XZ
        * Polygon(
            (17.4, plate_t),
            (17.4, plate_t + boss_h),
            (outer, plate_t + toe),
            (outer, plate_t),
        ),
        amount=thick / 2,
        both=True,
    )
    return Rot(0, 0, angle) * (bite + fin)


def _optional_number(value, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a number.") from exc


def section_geometry(
    shape: Shape, origin: tuple[float, float, float], normal: tuple[float, float, float]
):
    """Cut the solid with a plane and describe the section in that plane."""
    from aicad.models import SectionReport

    sketch, plane = _sketch_at(shape.impl, origin, normal)
    faces = list(sketch.faces())
    loops: list[SectionLoop] = []
    points: list[tuple[float, float]] = []
    for face in faces:
        outer = _loop_points(face.outer_wire(), plane)
        if len(outer) >= 3:
            loops.append(SectionLoop("outer", outer))
            points.extend(outer)
        for wire in face.inner_wires():
            hole = _loop_points(wire, plane)
            if len(hole) >= 3:
                loops.append(SectionLoop("hole", hole))
                points.extend(hole)
    if points:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
    else:
        width = 0.0
        height = 0.0
    thickness = _sketch_thickness(sketch) if faces else None
    area = float(sketch.area) if faces else 0.0
    if not faces:
        note = "The plane misses the part. Area is 0."
    else:
        note = (
            "Area is the material cut by this plane. min_thickness_mm is the narrowest "
            "width between roughly parallel edges in the section, or null if no parallel "
            "pair was measured. Loop coordinates are millimeters in the plane: x along "
            "plane_x_mm and y along plane_y_mm."
        )
    return SectionReport(
        area_mm2=round_mm(area),
        face_count=len(faces),
        plane_origin_mm=_vec(plane.origin),
        plane_normal=_vec(plane.z_dir),
        plane_x_mm=_vec(plane.x_dir),
        plane_y_mm=_vec(plane.y_dir),
        width_mm=round_mm(width),
        height_mm=round_mm(height),
        min_thickness_mm=None if thickness is None else round_mm(thickness),
        loops=loops,
        view=None,
        note=note,
    )


def _section_wall_thickness(shape: Shape) -> tuple[float | None, int]:
    impl = shape.impl
    bbox = impl.bounding_box()
    axes = (
        ((1.0, 0.0, 0.0), float(bbox.min.X), float(bbox.max.X)),
        ((0.0, 1.0, 0.0), float(bbox.min.Y), float(bbox.max.Y)),
        ((0.0, 0.0, 1.0), float(bbox.min.Z), float(bbox.max.Z)),
    )
    best: float | None = None
    count = 0
    for normal, low, high in axes:
        span = high - low
        if span < 1e-6:
            continue
        for fraction in _SECTION_FRACTIONS:
            offset = low + fraction * span
            origin = (normal[0] * offset, normal[1] * offset, normal[2] * offset)
            try:
                sketch, _plane = _sketch_at(impl, origin, normal)
            except Exception:
                continue
            count += 1
            if not sketch.faces():
                continue
            thickness = _sketch_thickness(sketch)
            if thickness is not None and (best is None or thickness < best):
                best = thickness
    return best, count


def _sketch_at(impl, origin, normal):
    from build123d import Plane, Vector
    from build123d import section as bd_section

    direction = Vector(*normal)
    if direction.length < 1e-9:
        raise GeometryError("Section normal must be non-zero.")
    plane = Plane(origin=Vector(*origin), z_dir=direction)
    return bd_section(impl, section_by=plane, height=0.0), plane


def _loop_points(wire, plane, limit: int = 160) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for edge in wire.edges():
        try:
            samples = edge.positions(deflection=0.4)
        except Exception:
            samples = [edge.position_at(0.0), edge.position_at(1.0)]
        for sample in samples:
            local = plane.to_local_coords(sample)
            point = (round(float(local.X), 4), round(float(local.Y), 4))
            if not points or point != points[-1]:
                points.append(point)
    if len(points) > limit:
        stride = max(1, len(points) // limit)
        points = points[::stride]
    return points


def _sketch_thickness(sketch, samples: int = 3) -> float | None:
    """Minimum width between roughly parallel edges of a planar section."""
    from build123d import Axis, Vector

    best: float | None = None
    epsilon = 0.05
    for face in sketch.faces():
        try:
            face_normal = face.normal_at().normalized()
        except Exception:
            continue
        edges = list(face.edges())
        for edge in edges:
            if edge.length < 0.3:
                continue
            for index in range(samples):
                parameter = (index + 0.5) / samples
                try:
                    point = edge.position_at(parameter)
                    tangent = edge.tangent_at(parameter)
                except Exception:
                    continue
                if tangent.length < 1e-9:
                    continue
                tangent = tangent.normalized()
                side = face_normal.cross(tangent)
                if side.length < 1e-9:
                    continue
                side = side.normalized()
                inward = None
                if face.is_inside(point + side * epsilon) and not face.is_inside(point - side * epsilon):
                    inward = side
                elif face.is_inside(point - side * epsilon) and not face.is_inside(point + side * epsilon):
                    inward = -side
                if inward is None:
                    continue
                origin = point + inward * epsilon
                axis = Axis(origin, inward)
                nearest: float | None = None
                for other in edges:
                    try:
                        other_tangent = other.tangent_at(0.5)
                    except Exception:
                        continue
                    if other_tangent.length < 1e-9:
                        continue
                    if abs(other_tangent.normalized().dot(tangent)) < 0.7:
                        continue
                    hits = other.intersect(axis)
                    if not hits:
                        continue
                    for hit in hits:
                        distance = (Vector(hit) - origin).dot(inward)
                        if distance > 0.02 and (nearest is None or distance < nearest):
                            nearest = distance
                if nearest is None:
                    continue
                thickness = nearest + epsilon
                if best is None or thickness < best:
                    best = thickness
    return best


def _mm(value: float | None) -> str:
    return "none" if value is None else f"{value:.4f}"

