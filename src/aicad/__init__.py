"""AI-first CAD: model a hard-body part, then see and check it."""

from aicad.document import Document
from aicad.errors import AICADError, DocumentError, GeometryError, PartNotFound, ScriptError
from aicad.kernel import FeatureLog, solid_from_sketch
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
    SketchReport,
    Vec3,
    WallReport,
)
from aicad.sketch import Sketch, set_spec_dimension, solve_sketch

__version__ = "0.3.0"

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
    "Sketch",
    "SketchReport",
    "Vec3",
    "WallReport",
    "__version__",
    "set_spec_dimension",
    "solid_from_sketch",
    "solve_sketch",
]
