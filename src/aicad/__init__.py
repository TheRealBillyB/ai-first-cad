"""AI-first CAD: model a hard-body part, then see and check it."""

from aicad.document import Document
from aicad.errors import AICADError, DocumentError, GeometryError, PartNotFound, ScriptError
from aicad.kernel import FeatureLog
from aicad.models import (
    BoundingBox,
    ClearanceReport,
    FeatureReport,
    FeatureStep,
    InspectReport,
    Measurement,
    PartInfo,
    RenderedView,
    SectionReport,
    Vec3,
    WallReport,
)

__version__ = "0.2.0"

__all__ = [
    "AICADError",
    "BoundingBox",
    "ClearanceReport",
    "Document",
    "DocumentError",
    "FeatureLog",
    "FeatureReport",
    "FeatureStep",
    "GeometryError",
    "InspectReport",
    "Measurement",
    "PartInfo",
    "PartNotFound",
    "RenderedView",
    "ScriptError",
    "SectionReport",
    "Vec3",
    "WallReport",
    "__version__",
]
