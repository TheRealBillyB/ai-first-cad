"""AI-first CAD: model a hard-body part, then see and check it."""

from aicad.document import Document
from aicad.errors import AICADError, DocumentError, GeometryError, PartNotFound, ScriptError
from aicad.models import (
    BoundingBox,
    ClearanceReport,
    InspectReport,
    Measurement,
    PartInfo,
    RenderedView,
    Vec3,
    WallReport,
)

__version__ = "0.1.0"

__all__ = [
    "AICADError",
    "BoundingBox",
    "ClearanceReport",
    "Document",
    "DocumentError",
    "GeometryError",
    "InspectReport",
    "Measurement",
    "PartInfo",
    "PartNotFound",
    "RenderedView",
    "ScriptError",
    "Vec3",
    "WallReport",
    "__version__",
]
