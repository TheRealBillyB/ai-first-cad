"""Geometry kernel adapter.

This is the only module that imports build123d or OpenCascade. The rest of
aicad talks to plain records and to :class:`Shape`, so a different kernel
could replace this file later.
"""

from __future__ import annotations

import io
import math
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from build123d import (
    Box,
    Cylinder,
    Face,
    Plane,
    Pos,
    Vector,
    export_step,
    export_stl,
    import_step,
)
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

from aicad.errors import GeometryError, ScriptError
from aicad.models import BoundingBox, Measurement, Vec3, round_mm

UNITS = "mm"
_INTERFERENCE_VOLUME = 1e-4
_NAME_IN_SCRIPT = "part"


@dataclass
class Shape:
    """An opaque solid (or a compound of solids) owned by the kernel."""

    _impl: object

    @property
    def impl(self):
        return self._impl


@dataclass
class TriangleMesh:
    """Tessellation used by the software renderer. Arrays are kernel-private."""

    vertices: np.ndarray
    normals: np.ndarray
    triangles: np.ndarray
    edges: list[np.ndarray]


def make_from_script(code: str) -> Shape:
    """Execute a build123d script and return the solid assigned to ``part``."""
    if not isinstance(code, str) or not code.strip():
        raise ScriptError("The script is empty. Assign the finished solid to `part`.")
    if len(code) > 200_000:
        raise ScriptError("The script is longer than 200000 characters.")

    namespace: dict[str, object] = {
        "__builtins__": __builtins__,
        "__name__": "aicad_script",
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
        return Shape(_require_solid(namespace[_NAME_IN_SCRIPT]))
    except GeometryError as exc:
        raise ScriptError(str(exc)) from exc


def make_from_ops(ops: list[dict]) -> Shape:
    """Build a solid from a short list of add/cut box and cylinder operations."""
    if not isinstance(ops, list) or not ops:
        raise GeometryError("ops must be a non-empty list of operations.")
    if len(ops) > 500:
        raise GeometryError("A part may have at most 500 operations.")

    solid = None
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            raise GeometryError(f"Operation {index} must be an object.")
        kind = op.get("op")
        if kind not in ("add", "cut"):
            raise GeometryError(
                f"Operation {index} has op {kind!r}. Use 'add' or 'cut'."
            )
        tool = _primitive(op, index)
        if kind == "add":
            solid = tool if solid is None else solid + tool
        else:
            if solid is None:
                raise GeometryError(
                    f"Operation {index} is a cut, but the part has no solid yet. Add one first."
                )
            solid = solid - tool
            if _volume(solid) <= 0 or len(solid.solids()) == 0:
                raise GeometryError(f"Operation {index} (cut) removed the entire part.")
    return Shape(_require_solid(solid))


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


def estimate_wall_thickness(
    shape: Shape, samples_per_edge: int = 4
) -> tuple[float | None, int, int, Vec3 | None]:
    """Return ``(minimum_mm, sample_count, samples_per_edge, thinnest_point)``.

    Rays start just inside each face and travel through the material. A hit
    counts only when the opposite face is roughly parallel, which is the local
    wall thickness. The minimum over the samples is an estimate: a thin spot
    between samples is invisible to this check.
    """
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

    if thinnest is None:
        return None, 0, n, None
    return round_mm(thinnest), accepted, n, thinnest_point


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

