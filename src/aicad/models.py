"""Plain records returned by the public API.

Nothing in this module knows about the geometry kernel. Numbers are in
millimeters, and values are rounded so an assistant is not distracted by
solver noise.
"""

from __future__ import annotations

from dataclasses import dataclass


def round_mm(value: float, digits: int = 6) -> float:
    """Round a measured length, area, or volume."""
    return round(float(value), digits)


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

    def to_dict(self) -> dict[str, object]:
        return {
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
        }


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

    def to_dict(self) -> dict[str, object]:
        return {
            "estimated_min_mm": self.estimated_min_mm,
            "threshold_mm": self.threshold_mm,
            "passed": self.passed,
            "sample_count": self.sample_count,
            "samples_per_edge": self.samples_per_edge,
            "thinnest_point_mm": None
            if self.thinnest_point_mm is None
            else self.thinnest_point_mm.to_dict(),
            "method": self.method,
            "note": self.note,
        }


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
        return {
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
        }


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

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "units": self.units,
            "volume_mm3": self.volume_mm3,
            "valid": self.valid,
            "watertight": self.watertight,
            "bounding_box": self.bounding_box.to_dict(),
        }


@dataclass(frozen=True)
class InspectReport:
    part: PartInfo
    measurement: Measurement
    wall: WallReport
    views: list[RenderedView]

    def to_dict(self) -> dict[str, object]:
        return {
            "part": self.part.to_dict(),
            "measurement": self.measurement.to_dict(),
            "wall": self.wall.to_dict(),
            "views": [view.to_dict() for view in self.views],
        }
