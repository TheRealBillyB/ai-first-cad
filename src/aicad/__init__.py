"""AI-first CAD: model a hard-body part, then see and check it."""

# Remove the working directory from the import path as early as possible.
# Starting from a project folder is supported only through the aicad command
# or `python -P -m aicad`.

import sys


def _module_invocation() -> str | None:
    """The -m target while Python is still locating that module."""

    frame = sys._getframe()
    while frame is not None:
        code = frame.f_code
        filename = code.co_filename.replace("\\", "/")
        if code.co_name == "_run_module_as_main" and (
            filename == "<frozen runpy>" or filename.endswith("/runpy.py")
        ):
            name = frame.f_locals.get("mod_name")
            if isinstance(name, str):
                return name
            return None
        frame = frame.f_back
    return None


def _same_directory(entry: str, cwd: str) -> bool:
    import os

    if not isinstance(entry, str):
        return False
    if entry == "" or entry == os.curdir:
        return True
    try:
        if os.path.samefile(entry, cwd):
            return True
    except OSError:
        pass
    try:
        return os.path.normcase(os.path.abspath(entry)) == os.path.normcase(os.path.abspath(cwd))
    except (OSError, ValueError):
        return False


def _imported_shadows(cwd: str) -> list[str]:
    import os

    try:
        entries = os.listdir(cwd)
    except OSError:
        return []
    labels: list[str] = []
    for entry in entries:
        if entry.startswith("."):
            continue
        path = os.path.join(cwd, entry)
        if entry.endswith(".py"):
            stem = entry[:-3]
            if stem.isidentifier() and os.path.isfile(path) and stem in sys.modules:
                labels.append(entry)
            continue
        if entry.isidentifier() and os.path.isdir(path) and entry in sys.modules:
            labels.append(entry + "/")
    labels.sort()
    return labels


def _keep_module_mode_off_the_working_directory() -> None:
    target = _module_invocation()
    if target != "aicad" and not (isinstance(target, str) and target.startswith("aicad.")):
        return
    if not sys.path:
        return
    import os

    cwd = os.getcwd()
    if not _same_directory(sys.path[0], cwd):
        return
    sys.path[:] = [entry for entry in sys.path if not _same_directory(entry, cwd)]
    project = os.path.isfile(os.path.join(cwd, "session.json")) or os.path.isdir(
        os.path.join(cwd, ".aicad")
    )
    if not project:
        return
    labels = _imported_shadows(cwd)
    if not labels:
        return
    if len(labels) == 1:
        detail = f"{labels[0]} matches a module Python already loaded."
    else:
        detail = "these match modules Python already loaded: " + ", ".join(labels) + "."
    sys.stderr.write(
        "Cannot start from this project folder because "
        + detail
        + "\nRun aicad instead, or run from another folder.\n"
    )
    sys.stderr.flush()
    raise SystemExit(1)


_keep_module_mode_off_the_working_directory()
del _keep_module_mode_off_the_working_directory
del _imported_shadows
del _same_directory
del _module_invocation

from aicad._version import __version__
from aicad.document import Document
from aicad.errors import (
    AICADError,
    DocumentError,
    GeometryError,
    MountError,
    PartNotFound,
    PlanError,
    ScriptError,
    SketchPlaneError,
)
from aicad.kernel import FeatureLog, solid_from_sketch
from aicad.fea import fea_available, list_materials, lookup_material, select_faces, solve_part
from aicad.models import (
    BoundingBox,
    ClearanceReport,
    FeatureReport,
    FeatureStep,
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
)
from aicad.sketch import Sketch, set_spec_dimension, solve_sketch

__all__ = [
    "AICADError",
    "BoundingBox",
    "ClearanceReport",
    "Document",
    "DocumentError",
    "FeatureLog",
    "FeatureReport",
    "FeatureStep",
    "FeaReport",
    "GeometryError",
    "InspectReport",
    "Measurement",
    "MountError",
    "PackagingOverlap",
    "PackagingPart",
    "PackagingReport",
    "PartInfo",
    "PartNotFound",
    "PlanError",
    "RenderedView",
    "ScriptError",
    "SectionReport",
    "Sketch",
    "SketchPlaneError",
    "SketchReport",
    "Vec3",
    "WallReport",
    "__version__",
    "fea_available",
    "list_materials",
    "lookup_material",
    "select_faces",
    "set_spec_dimension",
    "solid_from_sketch",
    "solve_part",
    "solve_sketch",
]
