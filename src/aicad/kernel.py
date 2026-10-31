"""Geometry kernel adapter.

build123d and OpenCascade are imported here. ``aicad.library`` imports
bd_warehouse for fastener sizes that are not in the local tables.
``aicad.catalog_build`` imports build123d to build those tables.
``aicad.sketch`` imports the SolveSpace solver. The rest of aicad talks to
plain records and to :class:`Shape`.
"""

from __future__ import annotations

import ctypes
import io
import math
import os
import sys
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aicad.paths import prepare_geometry_import

prepare_geometry_import()


def _pin_opencascade_to_this_thread() -> None:
    """Keep OpenCascade on this thread on macOS.

    The default pool is created with ``pthread_create`` and a 512 KiB stack.
    A boolean or a mesh that recurses there kills the process, and a pytest
    worker exits with no traceback. One pool thread means the caller does
    the work. A later import can grow that pool again, so a deep call pins
    it once more before it runs.
    """

    if sys.platform != "darwin":
        return
    from OCP.OSD import OSD_Parallel, OSD_ThreadPool

    # Prefer these threads over an auxiliary pool. A build without one
    # ignores the request. Either way the pool below is a single thread.
    OSD_Parallel.SetUseOcctThreads_s(True)
    pool = OSD_ThreadPool.DefaultPool_s(1)
    if pool.NbThreads() != 1 or pool.HasThreads() or pool.NbDefaultThreadsToLaunch() != 1:
        pool.Init(1)
        pool.SetNbDefaultThreadsToLaunch(1)


def _keep_opencascade_on_the_calling_thread() -> None:
    """Run OpenCascade on the caller on macOS.

    OpenCascade creates its pool with ``pthread_create`` and the default
    attributes. On macOS that stack is 512 KiB. A boolean or a mesh recurses
    further than that, the process is killed, and a pytest worker exits with
    no Python traceback. ``threading.stack_size`` does not affect those pool
    threads. One pool thread is the caller, so the work stays on the caller.

    That caller is a workspace request thread, an MCP tool thread, or a
    thread a design script starts, as well as the main thread. CPython on
    macOS gives those threads about 16 MiB when no size is set. The call
    below stores an explicit size of at least 8 MiB for threads started
    after this import. A sandboxed script runs on the child process main
    thread, which keeps the stack the process was started with. A caller
    whose own stack is only the process limit runs the deep call on a
    thread that has the larger stack.
    """
    from aicad.thread_stack import prepare_geometry_thread_stack

    prepare_geometry_thread_stack()
    _pin_opencascade_to_this_thread()


_keep_opencascade_on_the_calling_thread()
from build123d import (
    Axis,
    Box,
    Cylinder,
    Face,
    Plane,
    CenterArc,
    Line,
    Polygon,
    Spline,
    Pos,
    Rot,
    Sphere,
    Vector,
    export_step,
    export_stl,
    extrude,
    revolve,
)
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.TopAbs import TopAbs_EDGE, TopAbs_IN, TopAbs_ON
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Dir, gp_Lin, gp_Pnt, gp_Trsf

from aicad.errors import EdgeOpError, GeometryError, ScriptError
from aicad.paths import session_path_escapes, windows_trim_escapes
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
# A kilometer. Ordinary parts are millimeters to a few meters.
MAX_MM = 1_000_000.0
_WALL_PROBES_MM = (0.01, 0.002, 0.0004)
_WALL_HIT_FLOOR_MM = 1e-4
# A cone's opposite wall is not parallel. 0.5 still rejects a face the ray only grazes.
_WALL_ALIGNMENT = 0.5
# Parameter-domain edges, where a cone apex or a knife tip sits. The regular
# grid is (i+0.5)/n and never lands on that border.
_WALL_BORDER = (0.02, 0.98)


def finite_number(
    value,
    label: str,
    *,
    bound: float | None = MAX_MM,
    positive: bool = False,
    unit: str = "mm",
) -> float:
    """Reject NaN, infinity, and sizes past ordinary part dimensions."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a finite number.") from exc
    if isinstance(value, bool) or not math.isfinite(number):
        raise GeometryError(f"{label} is not a finite number.")
    if positive and number <= 0:
        raise GeometryError(f"{label} must be a positive number.")
    if bound is not None and abs(number) > bound:
        raise GeometryError(
            f"{label} is {number:.12g} {unit}, past the {bound:.12g} {unit} limit. "
            "Ordinary parts stay within a kilometer."
        )
    return number


def finite_vec3(
    value,
    label: str,
    *,
    bound: float | None = MAX_MM,
    positive: bool = False,
    unit: str = "mm",
) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError(f"{label} must be a list of three numbers.")
    return tuple(
        finite_number(component, label, bound=bound, positive=positive, unit=unit) for component in value
    )


def _fflush_c_stdio() -> None:
    """Flush C stdio. OpenCascade writes past Python's ``sys.stdout``.

    ``ctypes.CDLL(None)`` is the process C library on POSIX. On Windows the
    geometry library is linked to the Universal CRT, and ``CDLL(None)`` does
    not load one, so the flush has to name that runtime. A buffered error
    flushed after the descriptor is restored would land on the real stdout.
    """
    names: tuple[str | None, ...]
    if os.name == "nt":
        names = ("ucrtbase", "api-ms-win-crt-stdio-l1-1-0", "msvcrt")
    else:
        names = (None,)
    for name in names:
        try:
            ctypes.CDLL(name).fflush(None)
        except Exception:
            continue


@contextmanager
def divert_native_stdio(*, stderr: bool = False):
    """Point fd 1 (and, when asked, fd 2) at ``os.devnull`` for one native call.

    The null device is ``os.devnull`` (``nul`` on Windows). The original
    descriptors are duplicated and put back afterwards, on Windows the same
    way as on POSIX, so a later JSON-RPC write still reaches the real stdout.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    _fflush_c_stdio()
    flags = os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        # A text-mode nul handle cannot replace a binary stdout on Windows.
        flags |= os.O_BINARY
    saved_out = os.dup(1)
    saved_err: int | None = None
    null_fd: int | None = None
    try:
        if stderr:
            saved_err = os.dup(2)
        from aicad.sandbox import open_null_device

        null_fd = open_null_device(flags)
        os.dup2(null_fd, 1)
        if saved_err is not None:
            os.dup2(null_fd, 2)
        yield
    finally:
        # Flush while fd 1 still points at the null device, then put the
        # original descriptor back. Restoring first would emit the buffered
        # native text onto the protocol stream. This runs even when the
        # second dup2 fails, so a Windows handle error cannot leave fd 1
        # pointed at the null device.
        if null_fd is not None:
            _fflush_c_stdio()
            os.dup2(saved_out, 1)
            if saved_err is not None:
                os.dup2(saved_err, 2)
            os.close(null_fd)
        if saved_err is not None:
            os.close(saved_err)
        os.close(saved_out)


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


@dataclass(frozen=True)
class ProjectedCurve:
    """One polyline from a hidden-line view, in millimetres on the page.

    ``kind`` is ``visible``, ``smooth`` (a tangent edge, drawn thin), or
    ``hidden``. X is to the right and Y is up.
    """

    kind: str
    points: tuple[tuple[float, float], ...]


@dataclass
class TriangleMesh:
    """Tessellation used by the software renderer. Arrays are kernel-private."""

    vertices: np.ndarray
    normals: np.ndarray
    triangles: np.ndarray
    edges: list[np.ndarray]
    colors: np.ndarray | None = None


def make_from_script(code: str, workspace: str | Path | None = None) -> Shape:
    """Run a build123d script and return the solid assigned to ``part``.

    Outside the sandbox child this starts a confined process. The child calls
    :func:`_execute_part_script` directly so it does not confine itself twice.
    """
    from aicad.sandbox_state import in_sandbox

    if in_sandbox():
        return _execute_part_script(code)
    from aicad.sandbox import run_part_script

    root = Path(workspace) if workspace is not None else None
    return run_part_script(code, root)


def _execute_part_script(code: str) -> Shape:
    """Run a part script in the current process. Only the sandbox child calls this."""
    if not isinstance(code, str) or not code.strip():
        raise ScriptError("The script is empty. Assign the finished solid to `part`.")
    if len(code) > 200_000:
        raise ScriptError("The script is longer than 200000 characters.")

    from aicad.sandbox import script_builtins

    log = FeatureLog()
    prelude: dict[str, object] = {"__builtins__": __builtins__, "__name__": "aicad_script"}
    try:
        with redirect_stdout(io.StringIO()):
            exec("from build123d import *\nimport math", prelude)  # noqa: S102
    except Exception as exc:
        raise ScriptError(_format_script_error(code, exc)) from exc
    namespace: dict[str, object] = {
        "__builtins__": script_builtins(),
        "__name__": "aicad_script",
        "feature": log,
    }
    for key, value in prelude.items():
        if key not in ("__builtins__", "__name__"):
            namespace[key] = value
    try:
        with redirect_stdout(io.StringIO()):
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
        _apply_model_op(log, op, index)
    return Shape(_require_solid(log.current), features=log.report())


def prefix_from_ops(ops: list[dict]) -> tuple[Shape | None, dict[str, str]]:
    """Build operations until one fails.

    The solid is the last successful step. The failing step's id maps to its
    error. Later steps are not built. An empty list or a failure of the first
    step returns no solid.
    """
    if not isinstance(ops, list) or not ops:
        return None, {"op/0": "ops must be a non-empty list of operations."}
    if len(ops) > 500:
        return None, {"op/0": "A part may have at most 500 operations."}
    log = FeatureLog()
    errors: dict[str, str] = {}
    for index, op in enumerate(ops):
        try:
            _apply_model_op(log, op, index)
        except GeometryError as exc:
            errors[f"op/{index}"] = str(exc)
            break
    if log.current is None:
        return None, errors
    return Shape(_require_solid(log.current), features=log.report()), errors


def _apply_model_op(log: FeatureLog, op: dict, index: int) -> None:
    if not isinstance(op, dict):
        raise GeometryError(f"Operation {index} must be an object.")
    kind = op.get("op")
    if kind not in ("add", "cut"):
        raise GeometryError(f"Operation {index} has op {kind!r}. Use 'add' or 'cut'.")
    tool = _primitive(op, index)
    name = op.get("name") or f"{kind}_{index + 1}"
    if not isinstance(name, str) or not name.strip():
        raise GeometryError(f"Operation {index} name must be a non-empty string.")
    if kind == "add":
        log.add(name, tool)
        return
    if log.current is None:
        raise GeometryError(
            f"Operation {index} is a cut, but the part has no solid yet. Add one first."
        )
    log.cut(name, tool)
    if _volume(log.current) <= 0 or len(log.current.solids()) == 0:
        raise GeometryError(f"Operation {index} (cut) removed the entire part.")


_EDGE_REF_TOLERANCE_MM = 1.0


def edge_indexes(shape: Shape, spec, label: str) -> list[int]:
    """Edge indexes for a fillet or chamfer.

    ``spec`` is a list of indexes, or edge refs such as ``edge/line/5,0,2``.
    An index past the end of the solid, or a ref that matches no nearby edge,
    is a lost reference.
    """
    catalog = describe_edges(shape)
    if isinstance(spec, (str, int)) and not isinstance(spec, bool):
        spec = [spec]
    if not isinstance(spec, list) or not spec:
        raise GeometryError(f"{label} needs at least one edge.")
    if len(spec) > 500:
        raise GeometryError(f"{label} names too many edges.")
    found: list[int] = []
    for item in spec:
        index = _one_edge_index(catalog, item, label)
        if index not in found:
            found.append(index)
    return found


def apply_edge_ops(shape: Shape, ops: list[dict]) -> Shape:
    """Round or break edges. The input solid is left as it was.

    Each step is ``{"op": "fillet", "radius": mm, "edges": [...]}`` or
    ``{"op": "chamfer", "distance": mm, "edges": [...]}``. A radius or
    distance that does not fit, or an edge reference that does not match,
    raises :class:`EdgeOpError`.
    """
    if not isinstance(ops, list):
        raise GeometryError("Edge steps must be a list.")
    if len(ops) > 500:
        raise GeometryError("A part may have at most 500 edge steps.")
    current = shape
    for index, op in enumerate(ops):
        current = _apply_edge_op(current, op, index)
    return current


def apply_edge_ops_partial(shape: Shape, ops: list[dict]) -> tuple[Shape, dict[str, Exception]]:
    """Apply edge steps until one fails.

    The solid stays at the last step that fitted. The failing step id maps to
    the error. Later steps are not applied.
    """
    if not ops:
        return shape, {}
    if not isinstance(ops, list):
        raise GeometryError("Edge steps must be a list.")
    current = shape
    failures: dict[str, Exception] = {}
    for index, op in enumerate(ops):
        try:
            current = _apply_edge_op(current, op, index)
        except (EdgeOpError, GeometryError) as exc:
            failures[f"edge/{index}"] = exc
            break
    return current, failures


def _apply_edge_op(shape: Shape, op: dict, index: int) -> Shape:
    if not isinstance(op, dict):
        raise EdgeOpError(f"Edge step {index} must be an object.", "edge_lost")
    kind = op.get("op")
    raw_name = op.get("name")
    label = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else f"edge step {index + 1}"
    if kind == "fillet":
        amount = finite_number(op.get("radius"), f"{label} radius", positive=True)
    elif kind == "chamfer":
        amount = finite_number(op.get("distance"), f"{label} distance", positive=True)
    else:
        raise EdgeOpError(
            f"Edge step {index} has op {kind!r}. Use fillet or chamfer.",
            "edge_lost",
        )
    indexes = edge_indexes(shape, op.get("edges"), label)
    working = _copy_solid(shape.impl)
    edges = list(working.edges())
    if any(item >= len(edges) for item in indexes):
        raise EdgeOpError(
            f"The edge reference on {label} was lost. This solid has {len(edges)} edges.",
            "edge_lost",
        )
    chosen = [edges[item] for item in indexes]
    try:
        from build123d import chamfer, fillet

        if kind == "fillet":
            result = fillet(chosen, radius=amount)
        else:
            result = chamfer(chosen, length=amount)
    except EdgeOpError:
        raise
    except Exception as exc:
        if kind == "fillet":
            message = f"The fillet radius {amount:g} mm is too big for the selected edges."
            code = "radius_too_big"
        else:
            message = f"The chamfer distance {amount:g} mm is too big for the selected edges."
            code = "distance_too_big"
        raise EdgeOpError(message, code) from exc
    return Shape(_require_solid(result), features=shape.feature_report())


def _one_edge_index(catalog: list[dict], item, label: str) -> int:
    if isinstance(item, bool) or isinstance(item, float):
        raise GeometryError(f"{label} edge indexes must be whole numbers or edge refs.")
    if isinstance(item, int):
        if item < 0 or item >= len(catalog):
            raise EdgeOpError(
                f"The edge reference {item} on {label} was lost. This solid has {len(catalog)} edges.",
                "edge_lost",
            )
        return item
    if not isinstance(item, str) or not item.strip():
        raise GeometryError(f"{label} edge indexes must be whole numbers or edge refs.")
    from aicad.refs import ref_to_selector

    try:
        selector = ref_to_selector(item)
    except GeometryError as exc:
        raise EdgeOpError(f"The edge reference {item!r} on {label} was lost. {exc}", "edge_lost") from exc
    if selector.get("kind") != "edge":
        raise EdgeOpError(f"The edge reference {item!r} on {label} was lost. It is not an edge.", "edge_lost")
    kind = selector.get("edge_kind")
    pool = [edge for edge in catalog if edge.get("kind") == kind]
    if not pool:
        raise EdgeOpError(
            f"The edge reference {item!r} on {label} was lost. This solid has no {kind} edge.",
            "edge_lost",
        )
    at = selector["at"]
    best = min(pool, key=lambda edge: _point_distance(edge["midpoint_mm"], at))
    if _point_distance(best["midpoint_mm"], at) > _EDGE_REF_TOLERANCE_MM:
        raise EdgeOpError(
            f"The edge reference {item!r} on {label} was lost. No {kind} edge is near that point.",
            "edge_lost",
        )
    return int(best["index"])


def _point_distance(a, b) -> float:
    return math.sqrt(sum((float(a[index]) - float(b[index])) ** 2 for index in range(3)))


def _copy_solid(impl):
    """A fillet changes the solid it is given, so the previous one is copied first."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy

    copied = BRepBuilderAPI_Copy(impl.wrapped)
    copied.Perform(impl.wrapped)
    if not copied.IsDone():
        raise GeometryError("Could not copy the solid before an edge step.")
    from build123d import Part

    return Part(copied.Shape())


def measure(shape: Shape, material: str | None = None) -> Measurement:
    """Measure a solid. ``material`` adds mass as volume times that handbook density."""
    impl = shape.impl
    bbox = impl.bounding_box()
    low = bbox.min
    high = bbox.max
    size = bbox.size
    center = impl.center()
    mass_g = None
    material_id = None
    density = None
    if material is not None:
        from aicad.fea import lookup_material

        found = lookup_material(material)
        density = float(found["density_g_cm3"])
        material_id = str(found["id"])
        # mm^3 times g/cm^3 is grams after dividing by 1000.
        mass_g = round_mm(float(impl.volume) * density / 1000.0)
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
        mass_g=mass_g,
        material=material_id,
        density_g_cm3=density,
    )


def estimate_wall_thickness(shape: Shape, samples_per_edge: int = 4) -> WallEstimate:
    """Estimate minimum wall thickness from face-normal rays.

    A ray is the local thickness. ``method`` is ``ray+section`` when a planar
    section agrees within 0.05 mm. An axis-aligned slice of a round or rotated
    solid is a shorter chord than that thickness, and that chord does not lower
    the estimate. A reading that could not step into the solid is an upper bound
    and is marked capped, so a later threshold cannot treat it as a pass.
    """
    ray_mm, accepted, used, point, ray_capped = _ray_wall_thickness(shape, samples_per_edge)
    section_mm, section_count, section_capped = _section_wall_thickness(shape)
    minimum, capped, method = _thinnest(ray_mm, ray_capped, section_mm, section_capped)
    if method != "ray" and method != "ray+section":
        point = None
    note = (
        f"Minimum came from {method}. "
        f"Ray samples: {_mm(ray_mm)} mm. "
        f"Planar sections ({section_count} slices): {_mm(section_mm)} mm. "
        "Rays start 0.01 mm inside a face, including the border of the face where a "
        "taper comes to a point, and keep a hit only when the segment stays in the solid. "
        "Sections slice the part at 25%, 50%, and 75% of its extent along X, Y, and Z. "
        "A section width shorter than the ray reading is a chord of that slice, not a "
        "thinner wall, and it does not lower the estimate. Either method can miss a thin "
        "spot it does not cross."
    )
    if capped:
        note += (
            " The probe could not enter a wall, so this number is an upper bound "
            "and cannot pass a minimum."
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
        capped=capped,
    )


def _thinnest(
    ray_mm: float | None,
    ray_capped: bool,
    section_mm: float | None,
    section_capped: bool,
) -> tuple[float | None, bool, str]:
    """Local thickness, whether it is only an upper bound, and which method produced it.

    A capped reading is an upper bound from a probe that never entered the solid.
    It wins over a larger number, which is often the far side of a cavity. An
    uncapped ray is a thickness along the surface normal. A shorter section is
    not used in its place: that reading is the width of one axis-aligned slice.
    """
    if section_mm is not None and section_capped and (ray_mm is None or section_mm < ray_mm):
        return section_mm, True, "section"
    if ray_mm is not None and ray_capped and (section_mm is None or ray_mm <= section_mm):
        return ray_mm, True, "ray"
    if ray_mm is not None and not ray_capped:
        if section_mm is not None and not section_capped and abs(ray_mm - section_mm) <= 0.05:
            return ray_mm, False, "ray+section"
        return ray_mm, False, "ray"
    if section_mm is not None:
        # No ray confirmed this width, so a later threshold cannot treat it as a pass.
        return section_mm, True, "section"
    if ray_mm is not None:
        return ray_mm, ray_capped, "ray"
    return None, False, "none"


def _ray_wall_thickness(
    shape: Shape, samples_per_edge: int
) -> tuple[float | None, int, int, Vec3 | None, bool]:
    impl = shape.impl
    face_count = len(impl.faces())
    n = samples_per_edge
    while n > 1 and face_count * n * n > 8000:
        n -= 1

    measured: list[float] = []
    missed: list[float] = []
    thinnest_point: Vec3 | None = None
    thinnest_measured: float | None = None
    accepted = 0

    def consider(face, u: float, v: float, *, record_miss: bool) -> None:
        nonlocal accepted, thinnest_measured, thinnest_point
        try:
            point = face.position_at(u, v)
            normal = face.normal_at(u, v)
        except Exception:
            return
        if normal.length < 1e-9 or not face.is_inside(point):
            return
        inward, probe = _inward_probe(impl, point, normal)
        if inward is None or probe is None:
            # A border sample that cannot enter is not evidence the whole part is thin.
            # The regular grid still records that miss.
            if record_miss:
                missed.append(_WALL_PROBES_MM[-1])
            return
        origin = point + inward * probe
        distance = _first_parallel_hit(impl, origin, inward, probe)
        if distance is None:
            return
        thickness = distance + probe
        accepted += 1
        measured.append(thickness)
        if thinnest_measured is None or thickness < thinnest_measured:
            thinnest_measured = thickness
            thinnest_point = _vec(point)

    for face in impl.faces():
        for i in range(n):
            for j in range(n):
                consider(face, (i + 0.5) / n, (j + 0.5) / n, record_miss=True)
        for u in _WALL_BORDER:
            for v in _WALL_BORDER:
                consider(face, u, v, record_miss=False)
            consider(face, u, 0.5, record_miss=False)
            consider(face, 0.5, u, record_miss=False)
    minimum, capped = _combine_wall_samples(measured, missed)
    if capped:
        thinnest_point = None
    return minimum, accepted, n, thinnest_point, capped


def _combine_wall_samples(measured: list[float], missed: list[float]) -> tuple[float | None, bool]:
    if not measured and not missed:
        return None, False
    if not measured:
        return min(missed), True
    best = min(measured)
    if missed and best > 0.5 and min(missed) < best:
        return min(missed), True
    return best, False


def aabb_separated(a: Shape, b: Shape) -> bool:
    """True when the axis-aligned boxes cannot intersect. A touch still returns False."""
    a_low, a_high = _aabb(a)
    b_low, b_high = _aabb(b)
    for index in range(3):
        if a_high[index] < b_low[index] or b_high[index] < a_low[index]:
            return True
    return False


def overlap_volume(a: Shape, b: Shape) -> float:
    """Intersection volume in mm³. Separated boxes are not sent to the boolean."""
    if aabb_separated(a, b):
        return 0.0
    common = a.impl & b.impl
    overlap = float(common.volume) if common is not None else 0.0
    return 0.0 if overlap < 0 else overlap


def overlap_center(a: Shape, b: Shape) -> tuple[float, Vec3 | None]:
    """Intersection volume and the center of that solid.

    Separated boxes are not sent to the boolean. The center is omitted when
    the volume is only numerical noise. One call is enough for both numbers,
    so a hit does not cut the same pair again to find the center.
    """
    if aabb_separated(a, b):
        return 0.0, None
    common = a.impl & b.impl
    overlap = float(common.volume) if common is not None else 0.0
    if overlap < 0:
        overlap = 0.0
    if overlap <= _INTERFERENCE_VOLUME or common is None:
        return overlap, None
    return overlap, _vec(common.center())


def _aabb(shape: Shape) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    box = shape.impl.bounding_box()
    low, high = box.min, box.max
    return (float(low.X), float(low.Y), float(low.Z)), (float(high.X), float(high.Y), float(high.Z))


def clearance_between(a: Shape, b: Shape) -> tuple[float, float, Vec3 | None, Vec3 | None]:
    """Return intersection volume, surface gap, and the closest point on each solid.

    The gap is zero when the solids interfere. It is not a penetration depth.
    A pair that already intersects does not need a closest-point search: that
    search is discarded, and on a toothed solid it does not finish. The point
    is then the center of the intersection.
    """
    from aicad.thread_stack import call_with_geometry_stack

    return call_with_geometry_stack(_clearance_between_on_this_thread, a, b)


def _clearance_between_on_this_thread(a: Shape, b: Shape) -> tuple[float, float, Vec3 | None, Vec3 | None]:
    _pin_opencascade_to_this_thread()
    # Boxes that cannot meet have no intersection solid. The boolean would
    # only rediscover that, and on a toothed solid it is the slow part.
    if aabb_separated(a, b):
        overlap = 0.0
        common = None
    else:
        common = a.impl & b.impl
        overlap = float(common.volume) if common is not None else 0.0
        if overlap < 0:
            overlap = 0.0
    if overlap > _INTERFERENCE_VOLUME and common is not None:
        center = common.center()
        point = _vec(center)
        return round_mm(overlap), 0.0, point, point
    distance, first, second = a.impl.distance_to_with_closest_points(b.impl)
    return round_mm(overlap), round_mm(distance), _vec(first), _vec(second)


def aabb_gap_lower_bound(a: Shape, b: Shape) -> float:
    """Lower bound on the surface gap, in millimeters.

    Positive when the axis-aligned boxes are apart, by the largest per-axis
    separation. Negative when the boxes overlap. A true gap cannot be smaller
    than a positive result, so a broad phase can skip the boolean.
    """
    a_low, a_high = _aabb(a)
    b_low, b_high = _aabb(b)
    separations = [
        max(a_low[index] - b_high[index], b_low[index] - a_high[index]) for index in range(3)
    ]
    return max(separations)


# Edge-pair axes are exact and cheap while both solids have few edge directions.
# Past this, directions are sampled and the depth is an upper bound.
_PENETRATION_EDGE_BUDGET = 4096
_PENETRATION_SAMPLES = 96


def penetration_depth(a: Shape, b: Shape) -> dict:
    """Minimum translation that separates two solids, in millimeters.

    Convex polyhedra use the separating-axis theorem on their exact faces and
    edges, so the depth is the real interference. Any other solid uses the
    same theorem on the convex hull of its vertices or of a tessellation, which
    can be deeper than a local notch. ``direction`` is a unit vector that moves
    the second solid out of the first.
    """
    first = _solid_axes(a)
    second = _solid_axes(b)
    depth, direction, sampled = _separating_depth(first, second)
    convex = bool(first["convex"] and second["convex"] and not first["meshed"] and not second["meshed"])
    if depth <= 1e-9:
        depth = _intersection_extent(a, b)
        direction = [0.0, 0.0, 1.0]
        method = "intersection_extent"
    elif convex and not sampled:
        method = "separating_axis"
    elif sampled:
        method = "separating_axis_sampled"
    else:
        method = "convex_hull"
    return {
        "penetration_depth_mm": round_mm(max(0.0, depth)),
        "direction": [round_mm(component) for component in direction],
        "depth_method": method,
    }


def _solid_axes(shape: Shape) -> dict:
    planar = _planar_axes(shape)
    if planar is not None:
        return planar
    return _mesh_axes(shape)


def _planar_axes(shape: Shape) -> dict | None:
    """Exact vertices, face normals, and edge directions when every face is planar."""
    impl = shape.impl
    vertices = [_xyz(vertex) for vertex in impl.vertices()]
    if len(vertices) < 4:
        return None
    normals = []
    planes = []
    for face in impl.faces():
        adaptor = BRepAdaptor_Surface(face.wrapped)
        if adaptor.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
            return None
        center = face.center()
        origin = _xyz(center)
        normal = _outward_normal(face, center)
        if normal is None or abs(normal[0]) + abs(normal[1]) + abs(normal[2]) < 1e-12:
            return None
        normals.append(normal)
        planes.append((origin, normal))
    edges = []
    for edge in impl.edges():
        curve = BRepAdaptor_Curve(edge.wrapped)
        if curve.GetType() != GeomAbs_CurveType.GeomAbs_Line:
            return None
        start = curve.Value(curve.FirstParameter())
        end = curve.Value(curve.LastParameter())
        edges.append((end.X() - start.X(), end.Y() - start.Y(), end.Z() - start.Z()))
    return {
        "vertices": np.asarray(vertices, dtype=np.float64),
        "normals": normals,
        "edges": edges,
        "convex": _vertices_behind(vertices, planes),
        "meshed": False,
    }


def _mesh_axes(shape: Shape) -> dict:
    mesh = tessellate(shape)
    points = np.asarray(mesh.vertices, dtype=np.float64)
    normals = []
    edges = []
    for tri in mesh.triangles:
        a, b, c = points[int(tri[0])], points[int(tri[1])], points[int(tri[2])]
        normal = np.cross(b - a, c - a)
        if float(np.linalg.norm(normal)) > 1e-12:
            normals.append(normal)
        edges.extend((b - a, c - b, a - c))
    return {
        "vertices": points,
        "normals": normals,
        "edges": edges,
        "convex": False,
        "meshed": True,
    }


def _vertices_behind(vertices: list[tuple[float, float, float]], planes: list[tuple]) -> bool:
    """True when every vertex is on the inner side of every outward plane."""
    for origin, normal in planes:
        for vertex in vertices:
            outside = sum((vertex[index] - origin[index]) * normal[index] for index in range(3))
            if outside > 1e-5:
                return False
    return True


def _separating_depth(first: dict, second: dict) -> tuple[float, list[float], bool]:
    axes: list[np.ndarray] = []
    seen: set[tuple[float, ...]] = set()

    def add(vector) -> None:
        arr = np.asarray(vector, dtype=np.float64).reshape(3)
        length = float(np.linalg.norm(arr))
        if length < 1e-12:
            return
        arr = arr / length
        for component in arr:
            if component < -1e-12:
                arr = -arr
                break
            if component > 1e-12:
                break
        key = tuple(round(float(component), 8) for component in arr)
        if key in seen:
            return
        seen.add(key)
        axes.append(arr)

    for normal in list(first["normals"]) + list(second["normals"]):
        add(normal)
    edges_a = _unique_dirs(first["edges"])
    edges_b = _unique_dirs(second["edges"])
    sampled = False
    if len(edges_a) * len(edges_b) <= _PENETRATION_EDGE_BUDGET:
        for edge_a in edges_a:
            for edge_b in edges_b:
                add(np.cross(edge_a, edge_b))
    else:
        sampled = True
        for direction in _fibonacci_axes(_PENETRATION_SAMPLES):
            add(direction)
    for direction in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
        add(direction)
    verts_a = first["vertices"]
    verts_b = second["vertices"]
    if len(verts_a) == 0 or len(verts_b) == 0 or not axes:
        return 0.0, [0.0, 0.0, 1.0], sampled
    center_a = verts_a.mean(axis=0)
    center_b = verts_b.mean(axis=0)
    best = float("inf")
    best_axis = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    for axis in axes:
        projected_a = verts_a @ axis
        projected_b = verts_b @ axis
        overlap = float(min(projected_a.max(), projected_b.max()) - max(projected_a.min(), projected_b.min()))
        if overlap < best:
            best = overlap
            if float(np.dot(center_b - center_a, axis)) >= 0.0:
                best_axis = axis
            else:
                best_axis = -axis
    if best == float("inf"):
        best = 0.0
    return best, [float(component) for component in best_axis], sampled


def _unique_dirs(vectors) -> list[np.ndarray]:
    unique = []
    seen: set[tuple[float, ...]] = set()
    for vector in vectors:
        arr = np.asarray(vector, dtype=np.float64).reshape(3)
        length = float(np.linalg.norm(arr))
        if length < 1e-12:
            continue
        arr = arr / length
        for component in arr:
            if component < -1e-12:
                arr = -arr
                break
            if component > 1e-12:
                break
        key = tuple(round(float(component), 8) for component in arr)
        if key in seen:
            continue
        seen.add(key)
        unique.append(arr)
    return unique


def _fibonacci_axes(count: int) -> list[tuple[float, float, float]]:
    golden = math.pi * (3.0 - math.sqrt(5.0))
    axes = []
    for index in range(count):
        height = 1.0 - (2.0 * index + 1.0) / count
        radius = math.sqrt(max(0.0, 1.0 - height * height))
        theta = golden * index
        axes.append((math.cos(theta) * radius, height, math.sin(theta) * radius))
    return axes


def _intersection_extent(a: Shape, b: Shape) -> float:
    """Smallest side of the intersection box. Used when the axis test misses a hit."""
    common = a.impl & b.impl
    if common is None:
        return 0.0
    try:
        if float(common.volume) <= 0:
            return 0.0
        box = common.bounding_box()
    except Exception:
        return 0.0
    size = (
        float(box.max.X) - float(box.min.X),
        float(box.max.Y) - float(box.min.Y),
        float(box.max.Z) - float(box.min.Z),
    )
    return min(size)


def _xyz(point) -> tuple[float, float, float]:
    if hasattr(point, "X"):
        return (float(point.X), float(point.Y), float(point.Z))
    return (float(point[0]), float(point[1]), float(point[2]))


# A point this close to the wall is touching. Smaller than this is solver noise.
_PACK_TOUCH_MM = 1e-4
_PACK_VOLUME_MM3 = 1e-4
_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0


def make_envelope(spec: dict) -> Shape:
    """Build an envelope solid from a primitive or an extruded outline.

    A primitive is ``shape`` ``box``, ``cylinder``, or ``sphere``. A box uses
    ``size`` [x, y, z]. A cylinder uses ``radius`` and ``height``. A sphere
    uses ``radius``. ``center`` defaults to the origin. A cylinder also takes
    ``axis``.

    An outline is a closed polygon in a plane, extruded along ``axis`` between
    ``limit`` [low, high] or for ``length`` starting at the origin. Outline
    points are [x, y] in that plane. Local X is world +X projected onto the
    plane (world +Y when the axis is parallel to X). Local Y completes a
    right-handed frame with the axis. ``origin`` is the plane's origin before
    the low limit is applied.
    """
    if not isinstance(spec, dict):
        raise GeometryError("An envelope is a part name or an object.")
    if "outline" in spec:
        return Shape(_require_solid(_outline_solid(spec)))
    return Shape(_require_solid(_primitive_solid(spec, "envelope")))


def fit_in_envelope(part: Shape, envelope: Shape) -> dict:
    """Signed wall clearance of one solid inside an envelope solid.

    The envelope is the allowed volume. ``min_clearance_mm`` is the smallest
    signed distance from the part to that wall: positive inside, zero on the
    wall, negative when the part sticks out. ``max_protrusion_mm`` is how far
    the worst point sticks out. The point is on the part.

    A contained part uses the kernel distance to the envelope boundary. A
    polyhedron's protrusion is the farthest vertex outside the envelope.
    A curved face is the farthest point on that surface.
    """
    outside_volume = _boolean_volume(part.impl - envelope.impl)
    inside_volume = _boolean_volume(part.impl & envelope.impl)
    part_volume = _boolean_volume(part.impl)
    if part_volume <= _PACK_VOLUME_MM3:
        raise GeometryError("The part has no volume to pack.")
    contained = outside_volume <= _PACK_VOLUME_MM3 and inside_volume > _PACK_VOLUME_MM3
    if contained:
        clearance, at = _wall_clearance(part, envelope)
        return _fit_record(
            contained=True,
            clearance=clearance,
            at=at,
            protrusion=0.0,
            protrusion_at=None,
            outside_volume=0.0,
            inside_volume=inside_volume,
            method="boundary",
        )
    protrusion, at, method = _farthest_outside(part, envelope)
    if protrusion <= _PACK_TOUCH_MM and inside_volume > _PACK_VOLUME_MM3:
        clearance, point = _wall_clearance(part, envelope)
        return _fit_record(
            contained=True,
            clearance=clearance,
            at=point,
            protrusion=0.0,
            protrusion_at=None,
            outside_volume=0.0,
            inside_volume=inside_volume,
            method="boundary",
        )
    if at is None:
        raise GeometryError("Could not locate where the part leaves the envelope.")
    return _fit_record(
        contained=False,
        clearance=-protrusion,
        at=at,
        protrusion=protrusion,
        protrusion_at=at,
        outside_volume=outside_volume,
        inside_volume=inside_volume,
        method=method,
    )


def overlap_inside(a: Shape, b: Shape, envelope: Shape) -> dict | None:
    """Volume of ``a ∩ b`` that also lies inside the envelope, and a point there."""
    if aabb_separated(a, b):
        return None
    common = a.impl & b.impl
    if _boolean_volume(common) <= _PACK_VOLUME_MM3:
        return None
    inside = common & envelope.impl
    volume = _boolean_volume(inside)
    if volume <= _PACK_VOLUME_MM3:
        return None
    center = inside.center()
    return {
        "volume_mm3": volume,
        "at": (float(center.X), float(center.Y), float(center.Z)),
    }


def _fit_record(
    *,
    contained: bool,
    clearance: float,
    at: tuple[float, float, float],
    protrusion: float,
    protrusion_at: tuple[float, float, float] | None,
    outside_volume: float,
    inside_volume: float,
    method: str,
) -> dict:
    return {
        "contained": contained,
        "min_clearance_mm": clearance,
        "clearance_at_mm": at,
        "max_protrusion_mm": protrusion,
        "protrusion_at_mm": protrusion_at,
        "outside_volume_mm3": outside_volume,
        "inside_volume_mm3": inside_volume,
        "method": method,
    }


def _boolean_volume(impl) -> float:
    if impl is None:
        return 0.0
    try:
        volume = float(impl.volume)
    except Exception:
        return 0.0
    if not math.isfinite(volume) or volume <= 0:
        return 0.0
    return volume


def _wall_clearance(part: Shape, envelope: Shape) -> tuple[float, tuple[float, float, float]]:
    """Minimum distance from a contained solid to the envelope boundary."""
    shells = list(envelope.impl.shells())
    if not shells:
        raise GeometryError("The envelope has no wall to measure against.")
    if len(shells) == 1:
        target = shells[0].wrapped
    else:
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound

        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        for shell in shells:
            builder.Add(compound, shell.wrapped)
        target = compound
    tool = BRepExtrema_DistShapeShape(part.impl.wrapped, target)
    tool.Perform()
    if not tool.IsDone() or tool.NbSolution() < 1:
        raise GeometryError("Could not measure the gap between the part and the envelope wall.")
    point = tool.PointOnShape1(1)
    return float(tool.Value()), (float(point.X()), float(point.Y()), float(point.Z()))


class _OutsideMeter:
    """Distance from a point to an envelope solid. Zero when the point is inside."""

    def __init__(self, envelope: Shape):
        self._wrapped = envelope.impl.wrapped
        self._classifier = BRepClass3d_SolidClassifier(self._wrapped)

    def __call__(self, x: float, y: float, z: float) -> float:
        point = gp_Pnt(float(x), float(y), float(z))
        self._classifier.Perform(point, 1e-7)
        if self._classifier.State() in (TopAbs_IN, TopAbs_ON):
            return 0.0
        vertex = BRepBuilderAPI_MakeVertex(point).Vertex()
        tool = BRepExtrema_DistShapeShape(self._wrapped, vertex)
        tool.Perform()
        if not tool.IsDone():
            return 0.0
        distance = float(tool.Value())
        if not math.isfinite(distance) or distance < 0:
            return 0.0
        return distance


class _ProtrusionSearch:
    def __init__(self, part: Shape, envelope: Shape):
        self.meter = _OutsideMeter(envelope)
        self._part = BRepClass3d_SolidClassifier(part.impl.wrapped)
        self.best = 0.0
        self.point: tuple[float, float, float] | None = None
        self.method = "vertices"

    def vertex(self, x: float, y: float, z: float) -> float:
        return self._keep(x, y, z, "vertices", on_part=True)

    def surface(self, x: float, y: float, z: float, *, on_part: bool = True) -> float:
        return self._keep(x, y, z, "surface", on_part=on_part)

    def _keep(self, x: float, y: float, z: float, method: str, *, on_part: bool) -> float:
        if not on_part and not self._on_part(x, y, z):
            return 0.0
        distance = self.meter(x, y, z)
        if distance > self.best + 1e-9:
            self.best = distance
            self.point = (float(x), float(y), float(z))
            self.method = method
        return distance

    def _on_part(self, x: float, y: float, z: float) -> bool:
        self._part.Perform(gp_Pnt(float(x), float(y), float(z)), 1e-5)
        return self._part.State() in (TopAbs_IN, TopAbs_ON)


def _farthest_outside(part: Shape, envelope: Shape) -> tuple[float, tuple[float, float, float] | None, str]:
    """Farthest point of ``part`` outside ``envelope``, and the distance."""
    search = _ProtrusionSearch(part, envelope)
    for vertex in part.impl.vertices():
        search.vertex(vertex.X, vertex.Y, vertex.Z)
    if _polyhedron(part):
        return search.best, search.point, "vertices"
    directions = _support_directions(envelope)
    for edge in part.impl.edges():
        _maximize_edge(edge, search, directions)
    for face in part.impl.faces():
        _maximize_face(face, search, directions)
    return search.best, search.point, search.method if search.best > _PACK_TOUCH_MM else "vertices"


def _polyhedron(part: Shape) -> bool:
    for face in part.impl.faces():
        if BRepAdaptor_Surface(face.wrapped).GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
            return False
    return True


def _support_directions(envelope: Shape) -> list[tuple[float, float, float]]:
    directions: list[tuple[float, float, float]] = []
    for x in (-1.0, 0.0, 1.0):
        for y in (-1.0, 0.0, 1.0):
            for z in (-1.0, 0.0, 1.0):
                if x or y or z:
                    directions.append(_unit_tuple(x, y, z))
    normals: list[tuple[float, float, float]] = []
    for face in envelope.impl.faces():
        adaptor = BRepAdaptor_Surface(face.wrapped)
        if adaptor.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
            continue
        normal = _outward_normal(face, face.center())
        if normal is None:
            continue
        normals.append((float(normal[0]), float(normal[1]), float(normal[2])))
    directions.extend(normals)
    if len(normals) <= 24:
        for index, left in enumerate(normals):
            for right in normals[index + 1 :]:
                summed = (left[0] + right[0], left[1] + right[1], left[2] + right[2])
                length = math.sqrt(summed[0] ** 2 + summed[1] ** 2 + summed[2] ** 2)
                if length > 0.25:
                    directions.append((summed[0] / length, summed[1] / length, summed[2] / length))
    unique: list[tuple[float, float, float]] = []
    seen: set[tuple[int, int, int]] = set()
    for direction in directions:
        key = tuple(int(round(component * 1e5)) for component in direction)
        if key in seen or key == (0, 0, 0):
            continue
        seen.add(key)
        unique.append(direction)
    return unique


def _unit_tuple(x: float, y: float, z: float) -> tuple[float, float, float]:
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-15:
        return (0.0, 0.0, 0.0)
    return (x / length, y / length, z / length)


def _dot_dir(direction: tuple[float, float, float], vector) -> float:
    return direction[0] * float(vector.X()) + direction[1] * float(vector.Y()) + direction[2] * float(vector.Z())


def _wrap_param(parameter: float, low: float, high: float) -> float | None:
    span = high - low
    if span >= 2.0 * math.pi - 1e-6:
        period = 2.0 * math.pi
        while parameter < low - 1e-9:
            parameter += period
        while parameter > high + 1e-9:
            parameter -= period
        return min(high, max(low, parameter))
    for candidate in (parameter, parameter + 2.0 * math.pi, parameter - 2.0 * math.pi):
        if low - 1e-7 <= candidate <= high + 1e-7:
            return min(high, max(low, candidate))
    return None


def _maximize_edge(edge, search: _ProtrusionSearch, directions: list[tuple[float, float, float]]) -> None:
    adaptor = BRepAdaptor_Curve(edge.wrapped)
    kind = adaptor.GetType()
    if kind == GeomAbs_CurveType.GeomAbs_Line:
        return
    low = float(adaptor.FirstParameter())
    high = float(adaptor.LastParameter())
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        return
    if kind == GeomAbs_CurveType.GeomAbs_Circle:
        _circle_supports(adaptor, low, high, directions, search)
    count = _curve_samples(adaptor, low, high)
    values: list[tuple[float, float]] = []
    for index in range(count):
        parameter = low + (high - low) * index / (count - 1)
        point = adaptor.Value(parameter)
        distance = search.surface(point.X(), point.Y(), point.Z())
        values.append((parameter, distance))
    peaks = _parameter_peaks(values, adaptor)
    for left, right in peaks:
        _golden_edge(adaptor, left, right, search)


def _circle_supports(adaptor, low: float, high: float, directions, search: _ProtrusionSearch) -> None:
    position = adaptor.Circle().Position()
    x_axis = position.XDirection()
    y_axis = position.YDirection()
    for direction in directions:
        along_x = _dot_dir(direction, x_axis)
        along_y = _dot_dir(direction, y_axis)
        if along_x * along_x + along_y * along_y < 1e-16:
            continue
        parameter = _wrap_param(math.atan2(along_y, along_x), low, high)
        if parameter is None:
            continue
        point = adaptor.Value(parameter)
        search.surface(point.X(), point.Y(), point.Z())


def _curve_samples(adaptor, low: float, high: float) -> int:
    span = high - low
    if adaptor.GetType() == GeomAbs_CurveType.GeomAbs_Circle:
        radius = max(float(adaptor.Circle().Radius()), 1e-6)
        step = math.sqrt(max(1e-8, 0.16 / radius))
        count = int(math.ceil(span / max(step, 1e-3))) + 1
        return max(24, min(240, count))
    return max(16, min(80, int(span * 4) + 2))


def _parameter_peaks(values: list[tuple[float, float]], adaptor) -> list[tuple[float, float]]:
    if len(values) < 3:
        return []
    start = adaptor.Value(values[0][0])
    end = adaptor.Value(values[-1][0])
    periodic = _points_close(
        (start.X(), start.Y(), start.Z()),
        (end.X(), end.Y(), end.Z()),
    )
    count = len(values) - 1 if periodic else len(values)
    peaks: list[tuple[float, float, float]] = []
    for index in range(count):
        distance = values[index][1]
        if distance <= 0:
            continue
        previous = values[index - 1][1] if index > 0 else (values[-2][1] if periodic else -1.0)
        nxt = values[index + 1][1] if index + 1 < len(values) else (values[1][1] if periodic else -1.0)
        if distance + 1e-12 < previous or distance + 1e-12 < nxt:
            continue
        if index == 0:
            left = values[0][0]
            right = values[1][0]
        elif index + 1 >= len(values):
            left = values[-2][0]
            right = values[-1][0]
        else:
            left = values[index - 1][0]
            right = values[index + 1][0]
        if right > left:
            peaks.append((left, right, distance))
    peaks.sort(key=lambda item: item[2], reverse=True)
    return [(left, right) for left, right, _distance in peaks[:8]]


def _golden_edge(adaptor, low: float, high: float, search: _ProtrusionSearch) -> None:
    def value(parameter: float) -> float:
        point = adaptor.Value(parameter)
        return search.surface(point.X(), point.Y(), point.Z())

    if high - low < 1e-9:
        value((low + high) * 0.5)
        return
    left = low
    right = high
    c_param = right - _GOLDEN * (right - left)
    d_param = left + _GOLDEN * (right - left)
    c_value = value(c_param)
    d_value = value(d_param)
    for _ in range(48):
        if right - left < 1e-10:
            break
        if c_value >= d_value:
            right = d_param
            d_param = c_param
            d_value = c_value
            c_param = right - _GOLDEN * (right - left)
            c_value = value(c_param)
        else:
            left = c_param
            c_param = d_param
            c_value = d_value
            d_param = left + _GOLDEN * (right - left)
            d_value = value(d_param)


def _maximize_face(face, search: _ProtrusionSearch, directions: list[tuple[float, float, float]]) -> None:
    adaptor = BRepAdaptor_Surface(face.wrapped)
    kind = adaptor.GetType()
    if kind == GeomAbs_SurfaceType.GeomAbs_Plane:
        return
    u0 = float(adaptor.FirstUParameter())
    u1 = float(adaptor.LastUParameter())
    v0 = float(adaptor.FirstVParameter())
    v1 = float(adaptor.LastVParameter())
    if not all(math.isfinite(value) for value in (u0, u1, v0, v1)) or u1 < u0 or v1 < v0:
        return
    _face_supports(adaptor, kind, u0, u1, v0, v1, directions, search)
    nu, nv = _face_grid(kind)
    samples: list[tuple[float, float, float]] = []
    for i in range(nu):
        u = u0 if nu == 1 else u0 + (u1 - u0) * i / (nu - 1)
        for j in range(nv):
            v = v0 if nv == 1 else v0 + (v1 - v0) * j / (nv - 1)
            point = adaptor.Value(u, v)
            distance = search.surface(point.X(), point.Y(), point.Z())
            samples.append((u, v, distance))
    peaks: list[tuple[float, float, float]] = []
    for i in range(nu):
        for j in range(nv):
            u, v, distance = samples[i * nv + j]
            if distance <= 0:
                continue
            local = True
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if di == 0 and dj == 0:
                        continue
                    ii = i + di
                    jj = j + dj
                    if 0 <= ii < nu and 0 <= jj < nv and samples[ii * nv + jj][2] > distance + 1e-12:
                        local = False
            if local:
                peaks.append((u, v, distance))
    peaks.sort(key=lambda sample: sample[2], reverse=True)
    for u, v, _distance in peaks[:6]:
        _refine_face(adaptor, u, v, u0, u1, v0, v1, search)


def _face_grid(kind) -> tuple[int, int]:
    if kind == GeomAbs_SurfaceType.GeomAbs_Sphere:
        return 36, 19
    if kind in (
        GeomAbs_SurfaceType.GeomAbs_Cylinder,
        GeomAbs_SurfaceType.GeomAbs_Cone,
        GeomAbs_SurfaceType.GeomAbs_Torus,
        GeomAbs_SurfaceType.GeomAbs_SurfaceOfRevolution,
        GeomAbs_SurfaceType.GeomAbs_SurfaceOfExtrusion,
    ):
        return 36, 8
    return 16, 12


def _face_supports(adaptor, kind, u0, u1, v0, v1, directions, search: _ProtrusionSearch) -> None:
    if kind == GeomAbs_SurfaceType.GeomAbs_Cylinder:
        _cylinder_supports(adaptor, u0, u1, v0, v1, directions, search)
    elif kind == GeomAbs_SurfaceType.GeomAbs_Sphere:
        _sphere_supports(adaptor, u0, u1, v0, v1, directions, search)
    elif kind == GeomAbs_SurfaceType.GeomAbs_Cone:
        _cone_supports(adaptor, u0, u1, v0, v1, directions, search)
    elif kind == GeomAbs_SurfaceType.GeomAbs_Torus:
        _torus_supports(adaptor, u0, u1, v0, v1, directions, search)


def _cylinder_supports(adaptor, u0, u1, v0, v1, directions, search: _ProtrusionSearch) -> None:
    position = adaptor.Cylinder().Position()
    x_axis = position.XDirection()
    y_axis = position.YDirection()
    z_axis = position.Direction()
    for direction in directions:
        along_x = _dot_dir(direction, x_axis)
        along_y = _dot_dir(direction, y_axis)
        along_z = _dot_dir(direction, z_axis)
        if along_x * along_x + along_y * along_y < 1e-16:
            parameter = u0
        else:
            parameter = _wrap_param(math.atan2(along_y, along_x), u0, u1)
            if parameter is None:
                continue
        height = v1 if along_z >= 0 else v0
        point = adaptor.Value(parameter, height)
        search.surface(point.X(), point.Y(), point.Z())


def _sphere_supports(adaptor, u0, u1, v0, v1, directions, search: _ProtrusionSearch) -> None:
    sphere = adaptor.Sphere()
    center = sphere.Location()
    radius = float(sphere.Radius())
    position = sphere.Position()
    x_axis = position.XDirection()
    y_axis = position.YDirection()
    z_axis = position.Direction()
    for direction in directions:
        along_x = _dot_dir(direction, x_axis)
        along_y = _dot_dir(direction, y_axis)
        along_z = _dot_dir(direction, z_axis)
        sin_v = min(1.0, max(-1.0, along_z))
        v = math.asin(sin_v)
        u = _wrap_param(math.atan2(along_y, along_x), u0, u1)
        if u is None or v < v0 - 1e-7 or v > v1 + 1e-7:
            continue
        point = adaptor.Value(u, min(v1, max(v0, v)))
        # The support constructed from the center is the same point on a full sphere.
        search.surface(point.X(), point.Y(), point.Z())
        search.surface(
            float(center.X()) + radius * direction[0],
            float(center.Y()) + radius * direction[1],
            float(center.Z()) + radius * direction[2],
            on_part=False,
        )


def _cone_supports(adaptor, u0, u1, v0, v1, directions, search: _ProtrusionSearch) -> None:
    """A solid cone is convex, so the extreme is on a circular boundary of the face."""
    position = adaptor.Cone().Position()
    x_axis = position.XDirection()
    y_axis = position.YDirection()
    for direction in directions:
        along_x = _dot_dir(direction, x_axis)
        along_y = _dot_dir(direction, y_axis)
        if along_x * along_x + along_y * along_y < 1e-16:
            continue
        parameter = _wrap_param(math.atan2(along_y, along_x), u0, u1)
        if parameter is None:
            continue
        for height in (v0, v1):
            point = adaptor.Value(parameter, height)
            search.surface(point.X(), point.Y(), point.Z())


def _torus_supports(adaptor, u0, u1, v0, v1, directions, search: _ProtrusionSearch) -> None:
    torus = adaptor.Torus()
    position = torus.Position()
    x_axis = position.XDirection()
    y_axis = position.YDirection()
    z_axis = position.Direction()
    for direction in directions:
        along_x = _dot_dir(direction, x_axis)
        along_y = _dot_dir(direction, y_axis)
        along_z = _dot_dir(direction, z_axis)
        if along_x * along_x + along_y * along_y < 1e-16:
            u = u0
        else:
            u = _wrap_param(math.atan2(along_y, along_x), u0, u1)
            if u is None:
                continue
        radial = math.sqrt(max(0.0, 1.0 - along_z * along_z))
        # v is the angle around the tube. The outer side is v = 0 in gp_Torus
        # when the direction lies in the plane of the ring; use atan2.
        v = _wrap_param(math.atan2(along_z, radial if radial > 1e-12 else 1.0), v0, v1)
        if v is None:
            continue
        point = adaptor.Value(u, v)
        search.surface(point.X(), point.Y(), point.Z())


def _refine_face(adaptor, u: float, v: float, u0: float, u1: float, v0: float, v1: float, search: _ProtrusionSearch) -> None:
    step_u = max((u1 - u0) / 40.0, 1e-6)
    step_v = max((v1 - v0) / 40.0, 1e-6)
    best = search.surface(*_surface_xyz(adaptor, u, v))
    for _ in range(36):
        improved = False
        for du, dv in (
            (step_u, 0.0),
            (-step_u, 0.0),
            (0.0, step_v),
            (0.0, -step_v),
            (step_u, step_v),
            (step_u, -step_v),
            (-step_u, step_v),
            (-step_u, -step_v),
        ):
            uu = min(u1, max(u0, u + du))
            vv = min(v1, max(v0, v + dv))
            distance = search.surface(*_surface_xyz(adaptor, uu, vv))
            if distance > best + 1e-10:
                best = distance
                u, v = uu, vv
                improved = True
                break
        if improved:
            continue
        step_u *= 0.5
        step_v *= 0.5
        if step_u < 1e-9 and step_v < 1e-9:
            break


def _surface_xyz(adaptor, u: float, v: float) -> tuple[float, float, float]:
    point = adaptor.Value(u, v)
    return float(point.X()), float(point.Y()), float(point.Z())


def _points_close(a: tuple[float, float, float], b: tuple[float, float, float], tol: float = 1e-7) -> bool:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2 <= tol * tol


def _primitive_solid(spec: dict, label: str):
    kind = spec.get("shape", spec.get("primitive"))
    center = finite_vec3(spec.get("center", [0, 0, 0]), f"{label} center")
    if kind == "box":
        size = finite_vec3(spec.get("size"), f"{label} size", positive=True)
        solid = Pos(*center) * Box(size[0], size[1], size[2])
        return solid
    if kind == "cylinder":
        radius = finite_number(spec.get("radius"), f"{label} radius", positive=True)
        height = finite_number(spec.get("height"), f"{label} height", positive=True)
        axis = finite_vec3(spec.get("axis", [0, 0, 1]), f"{label} axis")
        length = math.sqrt(sum(component * component for component in axis))
        if length < 1e-9:
            raise GeometryError(f"{label} axis must be non-zero.")
        direction = tuple(component / length for component in axis)
        return Plane(origin=center, z_dir=direction) * Cylinder(radius, height)
    if kind == "sphere":
        radius = finite_number(spec.get("radius"), f"{label} radius", positive=True)
        return Pos(*center) * Sphere(radius)
    raise GeometryError(
        f"Unknown envelope shape {kind!r}. Use 'box', 'cylinder', 'sphere', or an outline."
    )


def _outline_solid(spec: dict):
    raw = spec.get("outline")
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        raise GeometryError("An outline needs at least three [x, y] points.")
    if len(raw) > 500:
        raise GeometryError("An outline may have at most 500 points.")
    points: list[tuple[float, float]] = []
    for index, point in enumerate(raw):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise GeometryError(f"Outline point {index} must be [x, y].")
        x = finite_number(point[0], f"outline point {index}")
        y = finite_number(point[1], f"outline point {index}")
        points.append((x, y))
    if _points_close((points[0][0], points[0][1], 0.0), (points[-1][0], points[-1][1], 0.0), tol=1e-9):
        points = points[:-1]
    if len(points) < 3:
        raise GeometryError("An outline needs at least three distinct points.")
    if _polygon_area(points) <= 1e-6:
        raise GeometryError("The outline has no area.")
    axis = finite_vec3(spec.get("axis", [0, 0, 1]), "outline axis")
    axis_length = math.sqrt(sum(component * component for component in axis))
    if axis_length < 1e-9:
        raise GeometryError("The outline axis must be non-zero.")
    axis_unit = tuple(component / axis_length for component in axis)
    origin = finite_vec3(spec.get("origin", [0, 0, 0]), "outline origin")
    if "limit" in spec and "length" in spec:
        raise GeometryError("Pass an outline limit or a length, not both.")
    if "limit" in spec:
        limit = spec["limit"]
        if not isinstance(limit, (list, tuple)) or len(limit) != 2:
            raise GeometryError("Outline limit is [low, high] along the axis.")
        low = finite_number(limit[0], "outline limit")
        high = finite_number(limit[1], "outline limit")
    elif "length" in spec:
        low = 0.0
        high = finite_number(spec["length"], "outline length", positive=True)
    else:
        raise GeometryError("An outline needs limit [low, high] or length.")
    if high <= low:
        raise GeometryError("Outline limit must run from low to high.")
    x_dir, _y_dir = _outline_axes(axis_unit)
    start = tuple(origin[index] + axis_unit[index] * low for index in range(3))
    try:
        face = Polygon(*points)
        prism = extrude(face, amount=high - low)
        plane = Plane(origin=start, x_dir=x_dir, z_dir=axis_unit)
        solid = plane.location * prism
    except GeometryError:
        raise
    except Exception as exc:
        raise GeometryError(f"Could not extrude the outline: {exc}") from exc
    if _boolean_volume(solid) <= _PACK_VOLUME_MM3:
        raise GeometryError("The outline did not produce a solid. The polygon may cross itself.")
    return solid


def _polygon_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    count = len(points)
    for index, point in enumerate(points):
        nxt = points[(index + 1) % count]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return abs(area) * 0.5


def _outline_axes(axis: tuple[float, float, float]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Local X and Y of an outline plane whose normal is ``axis``."""
    reference = (1.0, 0.0, 0.0)
    dot = axis[0]
    projected = (reference[0] - dot * axis[0], reference[1] - dot * axis[1], reference[2] - dot * axis[2])
    if projected[0] ** 2 + projected[1] ** 2 + projected[2] ** 2 < 1e-8:
        reference = (0.0, 1.0, 0.0)
        dot = axis[1]
        projected = (reference[0] - dot * axis[0], reference[1] - dot * axis[1], reference[2] - dot * axis[2])
    x_dir = _unit_tuple(*projected)
    y_dir = _unit_tuple(
        axis[1] * x_dir[2] - axis[2] * x_dir[1],
        axis[2] * x_dir[0] - axis[0] * x_dir[2],
        axis[0] * x_dir[1] - axis[1] * x_dir[0],
    )
    return x_dir, y_dir


def solid_inertia(shape: Shape) -> dict:
    """Volume, center of mass, and the inertia tensor about that center.

    The tensor is in millimeters to the fifth: multiply by density in kg/mm³
    for kg·mm². OpenCascade returns the physical tensor, products included.
    """
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.impl.wrapped, props)
    center = props.CentreOfMass()
    matrix = props.MatrixOfInertia()
    tensor = [
        [float(matrix.Value(row, col)) for col in (1, 2, 3)]
        for row in (1, 2, 3)
    ]
    return {
        "volume_mm3": float(props.Mass()),
        "center_mm": [float(center.X()), float(center.Y()), float(center.Z())],
        "inertia_mm5": tensor,
    }


def _prepare_export_parent(path: Path) -> None:
    from aicad.paths import ensure_real_directory

    try:
        ensure_real_directory(path.parent)
    except OSError as exc:
        raise GeometryError(f"Could not write {path}.") from exc


def _volume_of_step_bytes(data: bytes) -> float:
    """Volume of STEP bytes. A shell or a file this reader rejects is zero."""
    if not data or len(data) > MAX_STEP_BYTES:
        return 0.0
    try:
        loaded = _read_step_bytes(Path("part.step"), data)
    except (GeometryError, OSError, RuntimeError):
        return 0.0
    return _volume(loaded)


def _step_bytes(impl, *, write_pcurves: bool) -> bytes | None:
    """STEP bytes for ``impl`` when they read back as a solid."""
    buffer = io.BytesIO()
    try:
        written = bool(export_step(impl, buffer, write_pcurves=write_pcurves))
    except (OSError, RuntimeError, ValueError):
        return None
    if not written:
        return None
    data = buffer.getvalue()
    if _volume_of_step_bytes(data) <= 0:
        return None
    return data


def _solid_step_bytes(impl) -> bytes | None:
    """Bytes of a solid.

    The writer builds the file in memory and the volume check reads those
    bytes. A path is not opened for either step. Parametric curves are tried
    first. A shell is sewn and, if it is still empty, written again without
    those curves. A box is left unchanged.
    """
    data = _step_bytes(impl, write_pcurves=True)
    if data is not None:
        return data
    cleaned = _sewn_solid(impl)
    if cleaned is not None:
        data = _step_bytes(cleaned, write_pcurves=True)
        if data is not None:
            return data
    data = _step_bytes(impl, write_pcurves=False)
    if data is not None:
        return data
    if cleaned is None:
        return None
    return _step_bytes(cleaned, write_pcurves=False)


def _sewn_solid(impl):
    """Sew a solid whose faces are valid in memory but write as an empty shell.

    A flat-bottomed loft fused with a cylinder that sits tangent to that face
    is one of those solids. ``clean`` sews the shared face. A box is unchanged.
    """
    try:
        cleaned = impl.clean()
    except Exception:
        return None
    if cleaned is None or _volume(cleaned) <= 0:
        return None
    return cleaned


def export_step_file(shape: Shape, path: Path) -> None:
    from aicad.paths import nofollow_writes, publish_file

    def write(temporary: Path) -> None:
        data = _solid_step_bytes(shape.impl)
        if data is None:
            raise GeometryError(f"Could not write STEP file {path}.")
        temporary.write_bytes(data)

    if not nofollow_writes():
        _prepare_export_parent(path)
    publish_file(path, write)


def _without_hairline_edges(impl):
    """Drop edges too short to triangulate.

    A cylinder that sits tangent to a planar face leaves an edge at the
    contact point. The mesher then skips that face, or rejects the mesh.
    Edges under 0.01 mm are removed. A heal that changes the volume by more
    than a small fraction is discarded.
    """
    from build123d.topology import Shape as BuildShape
    from OCP.ShapeFix import ShapeFix_Wireframe

    try:
        original = float(impl.volume)
    except Exception:
        return impl
    try:
        fixer = ShapeFix_Wireframe(impl.wrapped)
        fixer.SetPrecision(0.01)
        fixer.FixSmallEdges()
        healed = BuildShape.cast(fixer.Shape())
    except Exception:
        return impl
    if healed is None:
        return impl
    try:
        if abs(float(healed.volume) - original) > max(0.5, abs(original) * 0.002):
            return impl
    except Exception:
        return impl
    return healed


def export_3mf_file(bodies: list[tuple[str, Shape]], path: Path) -> None:
    """Write one 3MF with a separate mesh object for each solid.

    The solids are not united, so a gap between them stays in the file.
    """
    from aicad.paths import nofollow_writes, publish_file

    def write(temporary: Path) -> None:
        _write_3mf(bodies, temporary)

    if not nofollow_writes():
        _prepare_export_parent(path)
    publish_file(path, write)


def _write_3mf(bodies: list[tuple[str, Shape]], path: Path) -> None:
    import copy
    import io

    from build123d import Mesher

    if not bodies:
        raise GeometryError("There is no solid to export.")
    mesher = Mesher()
    for name, shape in bodies:
        impl = _without_hairline_edges(copy.deepcopy(shape.impl))
        label = str(name)
        try:
            impl.label = label
        except Exception:
            label = ""
        mesher.add_shape(
            impl,
            linear_deflection=0.05,
            angular_deflection=0.2,
            part_number=label or None,
        )
    # The publish path may be a file descriptor name, which has no ".3mf" suffix.
    buffer = io.BytesIO()
    try:
        mesher.write_stream(buffer, "3mf")
        path.write_bytes(buffer.getvalue())
    except (OSError, RuntimeError, ValueError) as exc:
        raise GeometryError(f"Could not write 3MF file {path}.") from exc


def export_stl_file(
    shape: Shape,
    path: Path,
    linear_tolerance: float = 0.05,
    angular_tolerance: float = 0.2,
) -> None:
    from aicad.paths import nofollow_writes, publish_file

    def write(temporary: Path) -> None:
        import copy

        impl = _without_hairline_edges(copy.deepcopy(shape.impl))
        area = _surface_area(impl)
        _refuse_face_count(len(list(impl.faces())))
        linear = linear_for_surface(area, linear_tolerance)
        angular = _floor_angular(angular_tolerance)
        if not export_stl(
            impl,
            temporary,
            tolerance=linear,
            angular_tolerance=angular,
        ):
            raise GeometryError(f"Could not write STL file {path}.")

    if not nofollow_writes():
        _prepare_export_parent(path)
    publish_file(path, write)


# Entity names that point at another file. The check is case-insensitive.
# A comment cannot hide a name: the text is searched with comments removed
# as well as raw.
_STEP_EXTERNAL_MARKERS = (
    "document_file",
    "applied_document_reference",
    "applied_external_identification_assignment",
    "product_definition_with_associated_documents",
    "external_source",
)


# A shared STEP file is read on the host, outside the script file cap.
MAX_STEP_BYTES = 64 * 1024 * 1024
# A STEP list nested further than this is not given to the reader.
_MAX_STEP_LIST_DEPTH = 32
_MAX_TRIANGLES = 1_000_000
_MAX_FACES = 100_000
_MIN_ANGULAR = 0.05


def _refuse_large_mesh(count: int) -> None:
    if count > _MAX_TRIANGLES:
        raise GeometryError(f"That mesh has too many triangles. The limit is {_MAX_TRIANGLES}.")


def read_step_bytes(path: Path) -> bytes:
    """Read one STEP file. A link is not followed, and a file past the cap is not read."""
    from aicad.paths import read_nofollow_bytes

    return read_nofollow_bytes(path, maximum=MAX_STEP_BYTES, label="STEP file", error=GeometryError)


def import_step_file(path: Path) -> Shape:
    """Read one STEP file into a solid.

    Only this file is opened. A name inside it that points at another file is
    not followed, so a shared part cannot read or write outside the project
    by naming some other path. A file past the size cap is not read.

    A glTF, OBJ, COLLADA, or STL file is read the same way. An external buffer, image,
    or material library is opened only when it stays inside that file's folder,
    including a subfolder, and only through the shared file reader. The project
    path is not given to a geometry reader.
    """
    if path.suffix.lower() in {".gltf", ".glb", ".obj", ".dae", ".stl"}:
        from aicad.mesh_import import import_mesh_file

        return import_mesh_file(path)
    if not path.is_file():
        raise GeometryError(f"STEP file not found: {path}")
    return import_step_bytes(path, read_step_bytes(path))


def import_step_bytes(path: Path, data: bytes) -> Shape:
    """Build a solid from STEP bytes already read from ``path``.

    The reader is given those bytes. ``path`` is not opened again, and the
    reader is not given a temporary name to open.
    """
    if len(data) > MAX_STEP_BYTES:
        raise GeometryError(f"STEP file {path} is too large.")
    data = _accepted_step_bytes(path, data)
    _refuse_step_file_outside_folder(path, data)
    from aicad.thread_stack import call_with_geometry_stack

    try:
        solid = call_with_geometry_stack(_import_step_on_this_thread, path, data)
    except GeometryError:
        raise
    except Exception as exc:
        raise GeometryError(f"Could not read STEP file {path}: {exc}") from exc
    return Shape(solid)


def _import_step_on_this_thread(path: Path, data: bytes):
    """Read and sew one STEP solid. The caller has a stack large enough for it."""

    _pin_opencascade_to_this_thread()
    # The STEP parser logs through C stdout. That descriptor is the MCP
    # stream when `aicad serve` is running, so the bytes never reach it.
    with divert_native_stdio(stderr=True):
        imported = _read_step_bytes(path, data)
    try:
        return _require_solid(imported)
    except GeometryError as exc:
        raise GeometryError(f"Could not read STEP file {path}: {exc}") from exc


def _accepted_step_bytes(path: Path, data: bytes) -> bytes:
    """STEP bytes the reader may see.

    A leading byte-order mark and leading whitespace are not part of the
    exchange. A file that does not start with the STEP exchange marker, or a
    list nested past the cap, is not given to the reader. The message does
    not include the file body.
    """

    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    start = 0
    while start < len(data) and data[start] in b" \t\r\n":
        start += 1
    if start:
        data = data[start:]
    if not data.startswith(b"ISO-10303-21"):
        raise GeometryError(f"Could not read STEP file {path}.")
    text = data.decode("latin-1")
    depth = 0
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise GeometryError(f"Could not read STEP file {path}.")
            index = end + 2
            continue
        char = text[index]
        if char == "'":
            index += 1
            while index < length:
                if text[index] == "'":
                    if index + 1 < length and text[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise GeometryError(f"Could not read STEP file {path}.")
            continue
        if char == "(":
            depth += 1
            if depth > _MAX_STEP_LIST_DEPTH:
                raise GeometryError(f"Could not read STEP file {path}.")
        elif char == ")" and depth:
            depth -= 1
        index += 1
    return data


def _read_step_bytes(path: Path, data: bytes):
    """Read the solids in ``data``. The name passed to the reader is a label."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from build123d.topology import Shape as BuildShape

    reader = STEPControl_Reader()
    status = reader.ReadStream("part.step", io.BytesIO(data))
    if status != IFSelect_RetDone or reader.TransferRoots() < 1:
        raise GeometryError(f"Could not read STEP file {path}.")
    shape = reader.OneShape()
    if shape.IsNull():
        raise GeometryError(f"Could not read STEP file {path}.")
    return BuildShape.cast(shape)


def _refuse_step_file_outside_folder(path: Path, data: bytes) -> None:
    """Refuse a STEP file that names a path outside the file's own folder.

    An assembly file can name another STEP file. A name with a drive, a
    leading slash, a backslash, or ``..`` would leave the folder, so the
    file is not read at all. A name that stays in the folder is not opened
    either: only this file is read. ``data`` is the capped read of this file.
    The scan uses that same cap, so a larger buffer is not decoded.
    """
    if len(data) > MAX_STEP_BYTES:
        raise GeometryError(f"STEP file {path} is too large.")
    text = data.decode("latin-1")
    try:
        code, values = _step_code_and_strings(text)
    except ValueError as exc:
        if _step_mentions_external(text):
            raise GeometryError(
                f"Could not read STEP file {path}: it names other files in a form that was not accepted."
            ) from exc
        return
    if not _step_mentions_external(text, code):
        return
    folder = path.resolve().parent
    for value in values:
        if _step_string_leaves_folder(folder, value):
            raise GeometryError(
                f"Could not read STEP file {path}: it refers to a file outside its folder. It was not read."
            )


def _step_code_and_strings(text: str) -> tuple[str, list[str]]:
    """STEP text with comments removed, and every decoded string value."""
    code: list[str] = []
    values: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("unclosed comment")
            code.append(" ")
            index = end + 2
            continue
        if text[index] == "'":
            raw, index = _read_step_literal(text, index)
            pieces = [raw]
            while True:
                cursor = index
                while cursor < length and text[cursor] in " \t\r\n":
                    cursor += 1
                while cursor < length and text.startswith("/*", cursor):
                    end = text.find("*/", cursor + 2)
                    if end < 0:
                        raise ValueError("unclosed comment")
                    cursor = end + 2
                    while cursor < length and text[cursor] in " \t\r\n":
                        cursor += 1
                if cursor < length and text[cursor] == "'":
                    nxt, index = _read_step_literal(text, cursor)
                    pieces.append(nxt)
                    continue
                index = cursor
                break
            values.append(_decode_step_string("".join(pieces)))
            code.append("''")
            continue
        code.append(text[index])
        index += 1
    return "".join(code), values


def _read_step_literal(text: str, index: int) -> tuple[str, int]:
    """Content of one quoted literal. A doubled quote is an apostrophe."""
    index += 1
    chars: list[str] = []
    length = len(text)
    while index < length:
        if text[index] == "'":
            if index + 1 < length and text[index + 1] == "'":
                chars.append("'")
                index += 2
                continue
            return "".join(chars), index + 1
        chars.append(text[index])
        index += 1
    raise ValueError("unterminated string")


def _step_mentions_external(text: str, code: str | None = None) -> bool:
    """True when the file uses an entity that names another file."""
    for blob in (text, code):
        if blob is None:
            continue
        folded = blob.lower()
        if any(marker in folded for marker in _STEP_EXTERNAL_MARKERS):
            return True
    if code is not None:
        return False
    try:
        stripped = _strip_step_comments(text)
    except ValueError:
        return False
    folded = stripped.lower()
    return any(marker in folded for marker in _STEP_EXTERNAL_MARKERS)


def _strip_step_comments(text: str) -> str:
    parts: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("unclosed comment")
            parts.append(" ")
            index = end + 2
            continue
        parts.append(text[index])
        index += 1
    return "".join(parts)


def _decode_step_string(raw: str) -> str:
    """Decode ISO 10303-21 character escapes the way the STEP reader does.

    Newlines are removed, because the reader drops them before it uses the
    text as a file name. ``\\\\`` is one backslash. An unknown escape is
    refused so a directive cannot hide a path.
    """
    out: list[str] = []
    index = 0
    length = len(raw)
    hex_digits = "0123456789abcdefABCDEF"
    while index < length:
        char = raw[index]
        if char in "\n\r":
            index += 1
            continue
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 < length and raw[index + 1] == "\\":
            out.append("\\")
            index += 2
            continue
        token = raw[index:].lower()
        if token.startswith("\\x2\\") or token.startswith("\\x4\\"):
            width = 4 if token.startswith("\\x2\\") else 8
            index += 4
            digits: list[str] = []
            while index < length and not raw.lower().startswith("\\x0\\", index):
                digits.append(raw[index])
                index += 1
            if not raw.lower().startswith("\\x0\\", index):
                raise ValueError("unclosed character escape")
            index += 4
            blob = "".join(digits)
            if not blob or len(blob) % width != 0 or any(char not in hex_digits for char in blob):
                raise ValueError("bad character escape")
            for start in range(0, len(blob), width):
                code = int(blob[start : start + width], 16)
                if code > 0x10FFFF:
                    raise ValueError("bad character escape")
                out.append(chr(code))
            continue
        if token.startswith("\\x\\"):
            pair = raw[index + 3 : index + 5]
            if len(pair) != 2 or any(char not in hex_digits for char in pair):
                raise ValueError("bad character escape")
            out.append(chr(int(pair, 16)))
            index += 5
            continue
        if token.startswith("\\s\\"):
            if index + 3 >= length:
                raise ValueError("bad character escape")
            out.append(chr(ord(raw[index + 3]) + 128))
            index += 4
            continue
        if token.startswith("\\n\\") or token.startswith("\\t\\"):
            if token.startswith("\\t\\"):
                out.append("\t")
            index += 3
            continue
        if len(token) >= 4 and token[1] == "p" and token[3] == "\\":
            index += 4
            continue
        raise ValueError("bad character escape")
    return "".join(out)


def _step_string_leaves_folder(folder: Path, value: str) -> bool:
    """True when a decoded STEP string names something outside ``folder``."""
    if "\x00" in value:
        return True
    normalized = value.replace("\\", "/").replace("\n", "").replace("\r", "")
    if normalized == "":
        return False
    if session_path_escapes(normalized) or windows_trim_escapes(normalized):
        return True
    candidate = (folder / normalized).resolve()
    try:
        candidate.relative_to(folder)
    except ValueError:
        return True
    return False


def _surface_area(impl) -> float:
    try:
        area = abs(float(impl.area))
    except (TypeError, ValueError, AttributeError):
        return 0.0
    if not math.isfinite(area):
        return 0.0
    return area


def _refuse_face_count(count: int) -> None:
    if count > _MAX_FACES:
        raise GeometryError(
            f"That solid has too many faces to tessellate. The limit is {_MAX_FACES}."
        )


def linear_for_surface(area: float, linear: float) -> float:
    """A deflection whose triangle estimate stays inside the cap for ``area``."""

    if not math.isfinite(linear) or linear <= 0:
        linear = 0.05
    if not math.isfinite(area) or area <= 0:
        return linear
    if (2.0 * area) / (linear * linear) > _MAX_TRIANGLES * 0.99:
        linear = math.sqrt((2.0 * area) / (_MAX_TRIANGLES * 0.99))
    return linear


def _floor_angular(angular: float) -> float:
    if not math.isfinite(angular) or angular < _MIN_ANGULAR:
        return _MIN_ANGULAR
    return angular


def face_linear(area: float, base: float, remaining: int) -> float:
    """Deflection for one face, using the triangles still left in the budget."""

    linear = linear_for_surface(area, base)
    budget = max(int(remaining), 1)
    if math.isfinite(area) and area > 0:
        needed = math.sqrt((2.0 * area) / (budget * 0.99))
        if needed > linear:
            linear = needed
    return linear


def tessellation_linear(size_x: float, size_y: float, size_z: float, diagonal: float) -> float:
    """A deflection that stays inside the triangle cap before tessellation runs."""

    linear = min(0.12, max(0.04, diagonal * 0.0015))
    area = 2.0 * (
        abs(size_x * size_y) + abs(size_y * size_z) + abs(size_z * size_x)
    )
    if linear > 0 and area > 0 and (2.0 * area) / (linear * linear) > _MAX_TRIANGLES:
        # A little coarser than the exact cap, so rounding cannot go over it.
        linear = math.sqrt((2.0 * area) / (_MAX_TRIANGLES * 0.99))
    return linear


def tessellate(shape: Shape) -> TriangleMesh:
    """Mesh the solid and discretize its edges for rendering.

    Texture coordinates are not packed into an atlas. Print checks and
    pictures discard them, and the packer overflows on a solid with many
    faces, such as two gears in a housing.
    """
    from aicad.thread_stack import call_with_geometry_stack

    return call_with_geometry_stack(_tessellate_on_this_thread, shape)


def _tessellate_on_this_thread(shape: Shape) -> TriangleMesh:
    _pin_opencascade_to_this_thread()
    impl = shape.impl
    _refuse_face_count(len(list(impl.faces())))
    box = impl.bounding_box()
    diagonal = float(box.diagonal)
    size = box.size
    linear = tessellation_linear(float(size.X), float(size.Y), float(size.Z), diagonal)
    linear = linear_for_surface(_surface_area(impl), linear)
    vertices, triangles, normals, _uvs = impl.tessellate_with_uvs(
        linear, angular_tolerance=0.18, atlas_packing=False
    )
    if not triangles:
        raise GeometryError("The solid produced an empty mesh and cannot be rendered.")
    _refuse_large_mesh(len(triangles))

    points = np.array([(v.X, v.Y, v.Z) for v in vertices], dtype=np.float64)
    norms = np.array([(v.X, v.Y, v.Z) for v in normals], dtype=np.float64)
    faces = np.array(triangles, dtype=np.int32)
    deflection = max(linear, min(0.2, max(0.04, diagonal * 0.0015)))
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
    impl = shape.impl
    face_list = list(impl.faces())
    _refuse_face_count(len(face_list))
    faces = describe_faces(shape)
    edges = describe_edges(shape)
    diagonal = float(impl.bounding_box().diagonal) or 1.0
    base = min(0.28, max(0.08, diagonal * 0.004))
    positions: list[float] = []
    normals: list[float] = []
    triangle_face: list[int] = []
    remaining = _MAX_TRIANGLES
    tolerance = base
    for index, face in enumerate(face_list):
        tolerance = face_linear(_surface_area(face), base, remaining)
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
            remaining = _MAX_TRIANGLES - len(triangle_face)
            if len(triangle_face) > _MAX_TRIANGLES:
                _refuse_large_mesh(len(triangle_face))
    edge_payload = []
    for edge, record in zip(impl.edges(), edges):
        samples = edge.positions(deflection=base)
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


def face_pressure_triangles(
    shape: Shape, face_index: int, deflection: float
) -> list[tuple[float, tuple[float, float, float], tuple[tuple[float, float, float], ...]]]:
    """Tessellate one face for a pressure load.

    Each item is ``(area_mm2, outward_unit_normal, three_vertices)``. The normal
    points out of the material, matching :func:`describe_faces`. A curved face
    needs these triangles: one normal at the face center times the whole area
    is a single force, and a round hole then gets a sideways load instead of
    pressure.
    """
    faces = list(shape.impl.faces())
    if face_index < 0 or face_index >= len(faces) or deflection <= 0:
        return []
    face = faces[face_index]
    try:
        vertices, triangles = face.tessellate(deflection)
    except Exception:
        return []
    found = []
    for tri in triangles:
        try:
            corners = tuple(
                (float(vertices[int(index)].X), float(vertices[int(index)].Y), float(vertices[int(index)].Z))
                for index in tri
            )
        except Exception:
            continue
        if len(corners) != 3:
            continue
        a, b, c = corners
        ax, ay, az = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        bx, by, bz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        area = 0.5 * length
        if area < 1e-12 or length < 1e-12:
            continue
        normal = (nx / length, ny / length, nz / length)
        center = ((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0, (a[2] + b[2] + c[2]) / 3.0)
        try:
            reference = face.normal_at(Vector(*center))
            if normal[0] * reference.X + normal[1] * reference.Y + normal[2] * reference.Z < 0:
                normal = (-normal[0], -normal[1], -normal[2])
        except Exception:
            pass
        found.append((area, normal, corners))
    return found


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
    volume = _volume(obj)
    if len(obj.solids()) == 0 or not math.isfinite(volume) or volume <= 0:
        raise GeometryError(
            "The result has no solid volume. Assign a closed 3D solid to `part` "
            "(combine bodies with `part = a + b`)."
        )
    _require_sane_extent(obj)
    return obj


def _require_sane_extent(impl) -> None:
    """Reject a solid whose coordinates are not a mechanical part."""
    box = impl.bounding_box()
    values = (
        float(box.min.X),
        float(box.min.Y),
        float(box.min.Z),
        float(box.max.X),
        float(box.max.Y),
        float(box.max.Z),
    )
    for value in values:
        if not math.isfinite(value) or abs(value) > MAX_MM:
            shown = "not a finite number" if not math.isfinite(value) else f"{value:g} mm"
            raise GeometryError(
                f"This solid reaches {shown}, past the {MAX_MM:g} mm limit. "
                "Ordinary parts are millimeters to a few meters, and a size this large was not stored."
            )


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
    center = finite_vec3(op.get("center", [0, 0, 0]), f"operation {index} center")
    if kind == "box":
        if "axis" in op:
            raise GeometryError(
                f"Operation {index}: box is axis-aligned. Use a cylinder to pick an axis, "
                "or place a rotated box in a script."
            )
        size = finite_vec3(op.get("size"), f"operation {index} size", positive=True)
        solid = Box(size[0], size[1], size[2])
        if center != (0.0, 0.0, 0.0):
            solid = Pos(*center) * solid
        return solid
    if kind == "cylinder":
        radius = finite_number(op.get("radius"), f"operation {index} radius", positive=True)
        height = finite_number(op.get("height"), f"operation {index} height", positive=True)
        axis = finite_vec3(op.get("axis", [0, 0, 1]), f"operation {index} axis")
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
    return finite_vec3(value, label)


def _positive(value, label: str) -> float:
    return finite_number(value, label, positive=True)


def _inward_probe(impl, point, normal):
    """Direction into the solid, and how far that step stayed inside.

    The step is small enough to enter a 0.05 mm wall. A larger step used to
    start in the cavity and report the far side as the wall.
    """
    normal = normal.normalized()
    for probe in _WALL_PROBES_MM:
        # is_inside is true on the face itself. A step that only reaches the
        # opposite face has not entered the wall, so a thinner step is tried.
        into_negative = _strictly_inside(impl, point - normal * probe) and not _strictly_inside(
            impl, point + normal * probe
        )
        into_positive = _strictly_inside(impl, point + normal * probe) and not _strictly_inside(
            impl, point - normal * probe
        )
        if into_negative:
            return -normal, probe
        if into_positive:
            return normal, probe
    return None, None


def _first_parallel_hit(impl, origin, direction, probe: float) -> float | None:
    """Distance along ``direction`` to the first opposing face that stays in the solid."""
    floor = max(_WALL_HIT_FLOOR_MM, probe * 0.05)
    line = gp_Lin(
        gp_Pnt(origin.X, origin.Y, origin.Z),
        gp_Dir(direction.X, direction.Y, direction.Z),
    )
    intersector = BRepIntCurveSurface_Inter()
    intersector.Init(impl.wrapped, line, 1e-7)
    best: float | None = None
    while intersector.More():
        parameter = float(intersector.W())
        if parameter > floor:
            hit = Face(intersector.Face())
            try:
                hit_normal = hit.normal_at(Vector(intersector.Pnt()))
                aligned = abs(hit_normal.normalized().dot(direction))
            except Exception:
                aligned = 0.0
            if aligned > _WALL_ALIGNMENT:
                midpoint = origin + direction * (parameter * 0.5)
                if _strictly_inside(impl, midpoint) and (best is None or parameter < best):
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

    px, py, pz = finite_vec3(position, "position")
    rx, ry, rz = finite_vec3(rotation_deg, "rotation_deg", unit="deg")
    if all(abs(value) < 1e-12 for value in (px, py, pz, rx, ry, rz)):
        return shape
    moved = Pos(px, py, pz) * Rot(rx, ry, rz) * shape.impl
    return Shape(_require_solid(moved), features=shape.feature_report())


def compound(shapes: list[Shape]) -> Shape:
    """Group solids without fusing them, so a view can hide one behind another."""
    if not shapes:
        raise GeometryError("There is no solid to draw.")
    if len(shapes) == 1:
        return shapes[0]
    from build123d import Compound as BuildCompound

    try:
        made = BuildCompound(children=[shape.impl for shape in shapes])
    except Exception as exc:
        raise GeometryError(f"Could not combine the solids for a view: {exc}") from exc
    return Shape(made)


def view_frame(
    look: tuple[float, float, float], up: tuple[float, float, float]
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Screen axes for an orthographic view.

    ``look`` points from the camera into the scene. ``up`` is the hint for the
    top of the page. The returned pair is ``(right, up)``, both unit vectors.
    A later hidden-line picture uses the same pair, so a dimension sits on the
    outline it measures.
    """
    look_v = _unit3(float(look[0]), float(look[1]), float(look[2]))
    up_v = _unit3(float(up[0]), float(up[1]), float(up[2]))
    if look_v == [0.0, 0.0, 0.0] or up_v == [0.0, 0.0, 0.0]:
        raise GeometryError("A view needs a direction to look and a direction for up.")
    dot = look_v[0] * up_v[0] + look_v[1] * up_v[1] + look_v[2] * up_v[2]
    up_v = _unit3(
        up_v[0] - dot * look_v[0],
        up_v[1] - dot * look_v[1],
        up_v[2] - dot * look_v[2],
    )
    if up_v == [0.0, 0.0, 0.0]:
        raise GeometryError("The up direction is parallel to the viewing direction.")
    right = _unit3(
        look_v[1] * up_v[2] - look_v[2] * up_v[1],
        look_v[2] * up_v[0] - look_v[0] * up_v[2],
        look_v[0] * up_v[1] - look_v[1] * up_v[0],
    )
    return (tuple(right), tuple(up_v))  # type: ignore[return-value]


def project_points(
    points: list[tuple[float, float, float]] | list[list[float]],
    look: tuple[float, float, float],
    up: tuple[float, float, float],
) -> list[tuple[float, float]]:
    """Orthographic screen coordinates. X is right, Y is up, in millimetres."""
    right, screen_up = view_frame(look, up)
    projected = []
    for point in points:
        projected.append(
            (
                point[0] * right[0] + point[1] * right[1] + point[2] * right[2],
                point[0] * screen_up[0] + point[1] * screen_up[1] + point[2] * screen_up[2],
            )
        )
    return projected


def project_view(
    shape: Shape,
    look: tuple[float, float, float],
    up: tuple[float, float, float],
    *,
    hidden: bool = True,
) -> list[ProjectedCurve]:
    """Hidden-line picture of a solid or a compound.

    OpenCascade removes the hidden lines. ``hidden`` false keeps only what is
    in front, which is how the isometric view stays readable. Smooth tangent
    edges, such as where a fillet meets a face, come back as ``smooth``.
    The picture is orthographic. It is not a dimensioned drawing by itself.
    """
    right, _screen_up = view_frame(look, up)
    try:
        diagonal = float(shape.impl.bounding_box().diagonal)
    except Exception as exc:
        raise GeometryError(f"The solid has no bounding box to project: {exc}") from exc
    deflection = min(0.35, max(0.08, diagonal * 0.0008))
    try:
        algorithm = HLRBRep_Algo()
        algorithm.Add(shape.impl.wrapped)
        axes = gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(*look_unit(look)), gp_Dir(*right))
        algorithm.Projector(HLRAlgo_Projector(axes))
        algorithm.Update()
        algorithm.Hide()
        extracted = HLRBRep_HLRToShape(algorithm)
    except Exception as exc:
        raise GeometryError(f"Could not project the solid: {exc}") from exc

    groups = [
        ("visible", "VCompound"),
        ("visible", "OutLineVCompound"),
        ("smooth", "Rg1LineVCompound"),
    ]
    if hidden:
        groups.extend(
            [
                ("hidden", "HCompound"),
                ("hidden", "OutLineHCompound"),
                ("hidden", "Rg1LineHCompound"),
            ]
        )
    curves: list[ProjectedCurve] = []
    for kind, method_name in groups:
        getter = getattr(extracted, method_name, None)
        if getter is None:
            continue
        try:
            projected = getter()
        except Exception:
            continue
        if projected is None or projected.IsNull():
            continue
        curves.extend(_sample_projected(projected, kind, deflection))
    return curves


def look_unit(look: tuple[float, float, float]) -> tuple[float, float, float]:
    vector = _unit3(float(look[0]), float(look[1]), float(look[2]))
    if vector == [0.0, 0.0, 0.0]:
        raise GeometryError("A view needs a direction to look.")
    return (vector[0], vector[1], vector[2])


def _sample_projected(shape, kind: str, deflection: float) -> list[ProjectedCurve]:
    """Read HLR edges. Projector Y points down the page, so screen Y is flipped."""
    curves: list[ProjectedCurve] = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        try:
            edge = TopoDS.Edge(explorer.Current())
            curve = BRepAdaptor_Curve(edge)
            sampled = GCPnts_QuasiUniformDeflection(curve, deflection)
            if sampled.IsDone() and sampled.NbPoints() >= 2:
                points: list[tuple[float, float]] = []
                for index in range(1, sampled.NbPoints() + 1):
                    point = sampled.Value(index)
                    _append_projected(points, (float(point.X()), -float(point.Y())))
                if len(points) >= 2:
                    curves.append(
                        ProjectedCurve(
                            kind,
                            tuple((round(x, 3), round(y, 3)) for x, y in points),
                        )
                    )
        except Exception:
            pass
        explorer.Next()
    return curves


def _append_projected(points: list[tuple[float, float]], point: tuple[float, float]) -> None:
    if not points:
        points.append(point)
        return
    if (point[0] - points[-1][0]) ** 2 + (point[1] - points[-1][1]) ** 2 < 1e-8:
        return
    if len(points) >= 2 and _projected_collinear(points[-2], points[-1], point):
        points[-1] = point
        return
    points.append(point)


def _projected_collinear(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> bool:
    area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
    span = math.hypot(c[0] - a[0], c[1] - a[1])
    return span < 1e-9 or area / span < 0.02


def transform_shape(shape: Shape, matrix: list[list[float]]) -> Shape:
    """Place a solid by a 4×4 matrix. The matrix maps the solid's local frame into the world."""
    if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        raise GeometryError("A placement matrix is 4×4.")
    trsf = gp_Trsf()
    trsf.SetValues(
        float(matrix[0][0]),
        float(matrix[0][1]),
        float(matrix[0][2]),
        float(matrix[0][3]),
        float(matrix[1][0]),
        float(matrix[1][1]),
        float(matrix[1][2]),
        float(matrix[1][3]),
        float(matrix[2][0]),
        float(matrix[2][1]),
        float(matrix[2][2]),
        float(matrix[2][3]),
    )
    from build123d import Location

    moved = shape.impl.moved(Location(TopLoc_Location(trsf)))
    return Shape(moved, features=shape.feature_report())


def distance_to_point(shape: Shape, point: tuple[float, float, float] | list[float]) -> dict:
    """Gap from a point to the solid's boundary. Distance is 0 when the point is inside."""
    pnt = gp_Pnt(float(point[0]), float(point[1]), float(point[2]))
    classifier = BRepClass3d_SolidClassifier(shape.impl.wrapped, pnt, 1e-6)
    inside = classifier.State() in (TopAbs_IN, TopAbs_ON)
    vertex = BRepBuilderAPI_MakeVertex(pnt).Vertex()
    tool = BRepExtrema_DistShapeShape(shape.impl.wrapped, vertex)
    tool.Perform()
    gap = 0.0 if inside or not tool.IsDone() else float(tool.Value())
    return {"distance_mm": round_mm(gap), "inside": inside}


def solid_from_sketch(
    geometry: dict,
    steps: list[dict],
    feature: FeatureLog | None = None,
    frame: dict | None = None,
) -> Shape:
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

    ``z`` is the bottom of an extrusion or a cutter, along the sketch normal.
    A revolve spins the profile about the sketch X or Y axis. The profile must
    stay on one side of that axis. Consecutive points that are the ends of a
    sketch arc follow that arc. A closed spline through the profile points is
    that smooth curve, not the polygon of its points. A profile with only
    straight edges stays a polygon. ``frame`` places that solid on the sketch
    plane. ``cut_box`` and ``gusset`` stay in world coordinates.
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
        _apply_sketch_step(log, step, geometry, index, frame)
    return Shape(_require_solid(log.current), features=log.report())


def prefix_from_sketch(
    geometry: dict, steps: list[dict], frame: dict | None = None
) -> tuple[Shape | None, dict[str, str]]:
    """Build sketch steps until one fails. The solid is the last step that fitted."""
    if not isinstance(geometry, dict) or not isinstance(geometry.get("points"), dict):
        raise GeometryError(
            "solid_from_sketch needs solved sketch geometry, with points, lines, circles, and arcs."
        )
    if not isinstance(steps, list) or not steps:
        return None, {"step/0": "solid_from_sketch needs steps: extrude, cut, revolve, cut_box, or gusset."}
    if len(steps) > 500:
        return None, {"step/0": "A part may have at most 500 sketch steps."}
    log = FeatureLog()
    errors: dict[str, str] = {}
    for index, step in enumerate(steps):
        try:
            _apply_sketch_step(log, step, geometry, index, frame)
        except GeometryError as exc:
            errors[f"step/{index}"] = str(exc)
            break
    if log.current is None:
        return None, errors
    return Shape(_require_solid(log.current), features=log.report()), errors


def _apply_sketch_step(
    log: FeatureLog, step: dict, geometry: dict, index: int, frame: dict | None = None
) -> None:
    if not isinstance(step, dict):
        raise GeometryError(f"Sketch step {index} must be an object.")
    op = step.get("op")
    name = step.get("name") or f"{op}_{index + 1}"
    if not isinstance(name, str) or not name.strip():
        raise GeometryError(f"Sketch step {index} name must be a non-empty string.")
    if op in ("extrude", "revolve", "gusset"):
        log.add(name, _sketch_tool(step, geometry, index, name, frame))
        return
    if op in ("cut", "cut_box"):
        if log.current is None:
            raise GeometryError(
                f"Sketch step {index} ({name}) is a cut, but nothing has been added yet."
            )
        log.cut(name, _sketch_tool(step, geometry, index, name, frame))
        return
    raise GeometryError(
        f"Sketch step {index} has op {op!r}. Use extrude, cut, revolve, cut_box, or gusset."
    )


def _sketch_tool(step: dict, geometry: dict, index: int, name: str, frame: dict | None = None):
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
        return _on_sketch_plane(_revolve_profile(step, geometry, name), frame)
    if "circle" in step:
        return _on_sketch_plane(_circle_prism(step, geometry, name), frame)
    if "points" in step:
        return _on_sketch_plane(_polygon_prism(step, geometry, name), frame)
    raise GeometryError(
        f"{name}: an {op} needs `circle` or `points` from the solved sketch."
    )


def _on_sketch_plane(solid, frame: dict | None):
    """Map a solid built on world XY onto the sketch plane. XY at the origin stays put."""
    if not isinstance(frame, dict):
        return solid
    try:
        origin = tuple(float(component) for component in frame["origin"])
        x_dir = tuple(float(component) for component in frame["x"])
        normal = tuple(float(component) for component in frame["normal"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeometryError("A sketch frame needs origin, x, and normal.") from exc
    if (
        _near(origin, (0.0, 0.0, 0.0))
        and _near(x_dir, (1.0, 0.0, 0.0))
        and _near(normal, (0.0, 0.0, 1.0))
    ):
        return solid
    return Plane(origin=origin, x_dir=x_dir, z_dir=normal).location * solid


def _near(left: tuple[float, float, float], right: tuple[float, float, float]) -> bool:
    return all(abs(left[index] - right[index]) < 1e-9 for index in range(3))


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
        face = _profile_face(step, geometry, name, coords)
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
    signs = [sample[radial] for sample in _profile_samples(step, geometry, coords)]
    if min(signs) < -1e-9 and max(signs) > 1e-9:
        raise GeometryError(
            f"{name}: the profile crosses the {axis_name.upper()} axis. "
            "Keep every point on one side of the axis it revolves around."
        )
    axis = Axis.Y if axis_name == "y" else Axis.X
    try:
        face = _profile_face(step, geometry, name, coords)
        solid = revolve(face, axis=axis, revolution_arc=angle)
    except GeometryError:
        raise
    except Exception as exc:
        raise GeometryError(f"{name}: revolve failed. {exc}") from exc
    return solid


def _profile_face(step: dict, geometry: dict, name: str, coords: list[tuple[float, float]]):
    """A polygon when every edge is straight. An arc or a spline follows the curve."""
    ids = step.get("points")
    closed = _closed_spline(ids, geometry) if isinstance(ids, list) else None
    if closed is not None:
        try:
            return _sketch_face(
                Spline(*(_point_xy(geometry.get("points") or {}, point_id) for point_id in closed), periodic=True),
                name,
            )
        except GeometryError:
            raise
        except Exception as exc:
            raise GeometryError(f"{name}: could not build a face from that spline. {exc}") from exc
    edges = _boundary_arcs(geometry)
    if not isinstance(ids, list) or not (_profile_uses_arc(ids, edges) or _profile_uses_spline(ids, geometry)):
        return Plane.XY * Polygon(*coords)
    points = geometry.get("points") or {}
    wire = None
    count = len(ids)
    index = 0
    while index < count:
        run = _spline_run(ids, index, geometry)
        if run is not None and run[0] >= 1:
            span, order = run
            segment = Spline(*(_point_xy(points, point_id) for point_id in order))
            index += span
            if index >= count:
                index = count
        else:
            left = ids[index]
            right = ids[(index + 1) % count]
            pair = frozenset((left, right))
            matches = edges.get(pair) or []
            if matches:
                if len(matches) != 1:
                    raise GeometryError(f"{name}: more than one arc joins {left} and {right}.")
                segment = _boundary_arc(matches[0], left, right, name)
            else:
                segment = Line(_point_xy(points, left), _point_xy(points, right))
            index += 1
        wire = segment if wire is None else wire + segment
    try:
        return _sketch_face(wire, name)
    except GeometryError:
        raise
    except Exception as exc:
        raise GeometryError(f"{name}: could not build a face from that profile. {exc}") from exc


def _sketch_face(perimeter, name: str):
    """A face from a closed profile.

    Shape repair is applied when it finishes. A closed spline can already be a
    valid face when that repair raises, and that face is kept.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.ShapeFix import ShapeFix_Face
    from OCP.TopoDS import TopoDS
    from build123d import Face, Wire

    if isinstance(perimeter, Wire):
        wire = perimeter
    else:
        combined = Wire.combine(perimeter.edges())
        if not combined:
            raise GeometryError(f"{name}: could not build a closed wire from that profile.")
        wire = combined[0]
    builder = BRepBuilderAPI_MakeFace(wire.wrapped, True)
    builder.Build()
    if not builder.IsDone():
        raise GeometryError(f"{name}: could not build a face from that profile.")
    face = builder.Face()
    try:
        fixer = ShapeFix_Face(face)
        fixer.FixOrientation()
        fixer.Perform()
        face = TopoDS.Face(fixer.Result())
    except Exception:
        pass
    built = Face(face)
    try:
        if built.normal_at().Z < 0:
            built = -built
    except Exception:
        pass
    return built


def _closed_spline(ids: list, geometry: dict) -> list | None:
    from aicad.sketch_spline import same_cycle

    for spline in (geometry.get("splines") or {}).values():
        if not isinstance(spline, dict) or spline.get("construction") or not spline.get("closed"):
            continue
        order = [point_id for point_id in spline.get("points") or [] if isinstance(point_id, str)]
        if same_cycle(list(ids), order):
            return order
    return None


def _profile_uses_spline(ids: list, geometry: dict) -> bool:
    return _spline_run(ids, 0, geometry) is not None or any(
        _spline_run(ids, index, geometry) is not None for index in range(1, len(ids))
    )


def _spline_run(ids: list, index: int, geometry: dict):
    """Edges of one open spline starting at ``index``, as (edge count, point ids)."""
    count = len(ids)
    best_edges = 0
    best_points = None
    for spline in (geometry.get("splines") or {}).values():
        if not isinstance(spline, dict) or spline.get("construction") or spline.get("closed"):
            continue
        order = spline.get("points") or []
        edges = 0
        while index + edges < count and _spline_adjacent(
            order, ids[(index + edges) % count], ids[(index + edges + 1) % count]
        ):
            edges += 1
        if edges > best_edges:
            best_edges = edges
            best_points = [ids[(index + step) % count] for step in range(edges + 1)]
    if best_edges < 1 or best_points is None:
        return None
    return best_edges, best_points


def _spline_adjacent(order: list, left: str, right: str) -> bool:
    try:
        found = order.index(left)
    except ValueError:
        return False
    return found + 1 < len(order) and order[found + 1] == right


def _profile_uses_arc(ids: list, edges: dict) -> bool:
    count = len(ids)
    for index in range(count):
        if frozenset((ids[index], ids[(index + 1) % count])) in edges:
            return True
    return False


def _profile_samples(step: dict, geometry: dict, coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Profile points plus the high and low points of any arc on the boundary."""
    samples = list(coords)
    ids = step.get("points")
    if not isinstance(ids, list):
        return samples
    edges = _boundary_arcs(geometry)
    count = len(ids)
    for index in range(count):
        matches = edges.get(frozenset((ids[index], ids[(index + 1) % count]))) or []
        if len(matches) == 1:
            samples.extend(_arc_extrema(matches[0]))
    for spline in (geometry.get("splines") or {}).values():
        if not isinstance(spline, dict) or spline.get("construction"):
            continue
        order = spline.get("points") or []
        if not order or any(point_id not in ids for point_id in order):
            continue
        for sample in spline.get("samples") or []:
            if isinstance(sample, dict) and "x" in sample and "y" in sample:
                samples.append((float(sample["x"]), float(sample["y"])))
    return samples


def _boundary_arcs(geometry: dict) -> dict[frozenset, list[dict]]:
    arcs = geometry.get("arcs") or {}
    points = geometry.get("points") or {}
    grouped: dict[frozenset, list[dict]] = {}
    if not isinstance(arcs, dict) or not isinstance(points, dict):
        return grouped
    for arc in arcs.values():
        if not isinstance(arc, dict) or arc.get("construction"):
            continue
        start_id = arc.get("start_id") or _id_at(points, arc.get("start"))
        end_id = arc.get("end_id") or _id_at(points, arc.get("end"))
        if not isinstance(start_id, str) or not isinstance(end_id, str) or start_id == end_id:
            continue
        if start_id not in points or end_id not in points:
            continue
        stored = dict(arc)
        stored["start_id"] = start_id
        stored["end_id"] = end_id
        grouped.setdefault(frozenset((start_id, end_id)), []).append(stored)
    return grouped


def _boundary_arc(arc: dict, walk_from: str, walk_to: str, name: str):
    center = _xy_dict(arc.get("center"), f"{name} arc center")
    start = _xy_dict(arc.get("start"), f"{name} arc start")
    end = _xy_dict(arc.get("end"), f"{name} arc end")
    try:
        radius, start_angle, end_angle, sweep = _arc_sweep(center, start, end)
    except GeometryError as exc:
        raise GeometryError(f"{name}: {exc}") from exc
    if walk_from == arc.get("start_id") and walk_to == arc.get("end_id"):
        return CenterArc(center, radius, start_angle, sweep)
    if walk_from == arc.get("end_id") and walk_to == arc.get("start_id"):
        return CenterArc(center, radius, end_angle, -sweep)
    raise GeometryError(f"{name}: the arc does not join {walk_from} and {walk_to}.")


def _arc_sweep(center: tuple[float, float], start: tuple[float, float], end: tuple[float, float]):
    """Radius, start angle, end angle, and the counterclockwise sweep in degrees."""
    radius_start = math.hypot(start[0] - center[0], start[1] - center[1])
    radius_end = math.hypot(end[0] - center[0], end[1] - center[1])
    if radius_start <= 1e-3 or abs(radius_start - radius_end) > 1e-3:
        raise GeometryError("arc endpoints are not on one circle.")
    start_angle = math.degrees(math.atan2(start[1] - center[1], start[0] - center[0]))
    end_angle = math.degrees(math.atan2(end[1] - center[1], end[0] - center[0]))
    sweep = (end_angle - start_angle) % 360.0
    if sweep < 1e-3 or sweep > 360.0 - 1e-3:
        raise GeometryError("that arc has no sweep.")
    return radius_start, start_angle, end_angle, sweep


def _arc_extrema(arc: dict) -> list[tuple[float, float]]:
    try:
        center = _xy_dict(arc.get("center"), "center")
        start = _xy_dict(arc.get("start"), "start")
        end = _xy_dict(arc.get("end"), "end")
        radius, start_angle, _end_angle, sweep = _arc_sweep(center, start, end)
    except GeometryError:
        return []
    found = []
    for degrees in (0.0, 90.0, 180.0, 270.0):
        delta = (degrees - start_angle) % 360.0
        if delta <= sweep + 1e-6:
            radians = math.radians(degrees)
            found.append((center[0] + radius * math.cos(radians), center[1] + radius * math.sin(radians)))
    return found


def _point_xy(points: dict, point_id: str) -> tuple[float, float]:
    return _xy_dict(points.get(point_id), point_id)


def _xy_dict(point, label: str) -> tuple[float, float]:
    if not isinstance(point, dict):
        raise GeometryError(f"{label} is missing.")
    try:
        return (float(point["x"]), float(point["y"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise GeometryError(f"{label} is missing.") from exc


def _id_at(points: dict, coord) -> str | None:
    if not isinstance(coord, dict):
        return None
    try:
        x = float(coord["x"])
        y = float(coord["y"])
    except (KeyError, TypeError, ValueError):
        return None
    found = None
    for point_id, point in points.items():
        if not isinstance(point, dict):
            continue
        try:
            near = abs(float(point["x"]) - x) <= 1e-6 and abs(float(point["y"]) - y) <= 1e-6
        except (KeyError, TypeError, ValueError):
            continue
        if not near:
            continue
        if found is not None:
            return None
        found = point_id
    return found


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
    thickness, _section_capped = _sketch_thickness(sketch, solid=shape.impl) if faces else (None, False)
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


def _section_wall_thickness(shape: Shape) -> tuple[float | None, int, bool]:
    impl = shape.impl
    bbox = impl.bounding_box()
    axes = (
        ((1.0, 0.0, 0.0), float(bbox.min.X), float(bbox.max.X)),
        ((0.0, 1.0, 0.0), float(bbox.min.Y), float(bbox.max.Y)),
        ((0.0, 0.0, 1.0), float(bbox.min.Z), float(bbox.max.Z)),
    )
    measured: list[float] = []
    missed: list[float] = []
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
            thickness, capped = _sketch_thickness(sketch, solid=impl)
            if thickness is None:
                continue
            if capped:
                missed.append(thickness)
            else:
                measured.append(thickness)
    minimum, capped = _combine_wall_samples(measured, missed)
    return minimum, count, capped


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


def _strictly_inside(impl, point) -> bool:
    """True only in the material. A point on a face is not a wall reading."""
    classifier = BRepClass3d_SolidClassifier(
        impl.wrapped,
        gp_Pnt(float(point.X), float(point.Y), float(point.Z)),
        1e-7,
    )
    return classifier.State() == TopAbs_IN


def _strictly_on_face(face, point) -> bool:
    """True in the interior of a section face. A point on an edge is not inside."""
    classifier = BRepClass_FaceClassifier()
    classifier.Perform(
        face.wrapped,
        gp_Pnt(float(point.X), float(point.Y), float(point.Z)),
        1e-7,
    )
    return classifier.State() == TopAbs_IN


def _sketch_thickness(sketch, samples: int = 3, solid=None) -> tuple[float | None, bool]:
    """Minimum width between roughly parallel edges of a planar section."""
    from build123d import Axis, Vector

    measured: list[float] = []
    missed: list[float] = []
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
                probe = None
                for candidate in _WALL_PROBES_MM:
                    # A step that only reaches the opposite edge is not inside the section.
                    if _strictly_on_face(face, point + side * candidate) and not _strictly_on_face(
                        face, point - side * candidate
                    ):
                        inward = side
                        probe = candidate
                        break
                    if _strictly_on_face(face, point - side * candidate) and not _strictly_on_face(
                        face, point + side * candidate
                    ):
                        inward = -side
                        probe = candidate
                        break
                if inward is None or probe is None:
                    missed.append(_WALL_PROBES_MM[-1])
                    continue
                origin = point + inward * probe
                axis = Axis(origin, inward)
                floor = max(_WALL_HIT_FLOOR_MM, probe * 0.05)
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
                        if distance <= floor:
                            continue
                        midpoint = origin + inward * (distance * 0.5)
                        if solid is not None:
                            if not _strictly_inside(solid, midpoint):
                                continue
                        elif not face.is_inside(midpoint):
                            continue
                        if nearest is None or distance < nearest:
                            nearest = distance
                if nearest is None:
                    continue
                measured.append(nearest + probe)
    return _combine_wall_samples(measured, missed)


def _mm(value: float | None) -> str:
    return "none" if value is None else f"{value:.4f}"

