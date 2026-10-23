"""AI-first CAD: model a hard-body part, then see and check it."""

from aicad.document import Document
from aicad.errors import AICADError, DocumentError, GeometryError, PartNotFound, PlanError, ScriptError
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
    PartInfo,
    RenderedView,
    SectionReport,
    SketchReport,
    Vec3,
    WallReport,
)
from aicad.sketch import Sketch, set_spec_dimension, solve_sketch

__version__ = "0.9.0"

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
    "PartInfo",
    "PartNotFound",
    "PlanError",
    "RenderedView",
    "ScriptError",
    "SectionReport",
    "Sketch",
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
