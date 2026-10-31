"""Overall dimensions on a layout sheet meet the outline they measure.

The number is the axis-aligned size of the posed assembly. On a rotated
part the outline reaches each side at a point that is not the corner of
that box, and the extension has to start there.
"""

import math
from pathlib import Path

import ezdxf
import pytest

from aicad.document import Document
from aicad.drawing import _format_mm


def _box(document: Document, name: str, size: list[float]) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": size, "center": [0, 0, 0]}],
    )


def _assembly(document: Document, name: str, rotation: list[float]) -> None:
    document.create_assembly(
        {
            "name": name,
            "instances": [
                {
                    "name": "plate",
                    "part": "plate",
                    "placement": {"position": [0, 0, 0], "rotation_deg": rotation},
                }
            ],
            "joints": [],
        }
    )


def _segments(path: str):
    loaded = ezdxf.readfile(path)
    curves = []
    dimensions = []
    for entity in loaded.modelspace().query("LINE"):
        segment = (
            (float(entity.dxf.start.x), float(entity.dxf.start.y)),
            (float(entity.dxf.end.x), float(entity.dxf.end.y)),
        )
        if entity.dxf.layer == "DIMENSION":
            dimensions.append(segment)
        elif entity.dxf.layer in {"VISIBLE", "HIDDEN"}:
            curves.append(segment)
    return curves, dimensions


def _distance(point, segments) -> float:
    px, py = point
    best = math.inf
    for (ax, ay), (bx, by) in segments:
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        if length2 < 1e-12:
            gap = math.hypot(px - ax, py - ay)
        else:
            along = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
            gap = math.hypot(px - (ax + along * dx), py - (ay + along * dy))
        best = min(best, gap)
    return best


def _extensions_on_the_outline(path: str) -> int:
    """Dimension lines with one end on the drawn outline."""
    curves, dimensions = _segments(path)
    attached = 0
    for start, end in dimensions:
        if min(_distance(start, curves), _distance(end, curves)) <= 1.0:
            attached += 1
    return attached


def test_a_flat_plate_keeps_six_extensions_on_the_outline(tmp_path):
    document = Document(tmp_path)
    _box(document, "plate", [80, 12, 40])
    _assembly(document, "flat", [0, 0, 0])
    report = document.export_drawing("flat", scale="1:1")
    sizes = {item["axis"]: item["mm"] for item in report["dimensions"]}
    assert sizes == {"X": pytest.approx(80), "Y": pytest.approx(12), "Z": pytest.approx(40)}
    texts = [entity.dxf.text for entity in ezdxf.readfile(report["files"]["dxf"]).modelspace().query("TEXT")]
    assert _format_mm(80) in texts
    assert _format_mm(12) in texts
    assert _format_mm(40) in texts
    assert _extensions_on_the_outline(report["files"]["dxf"]) == 6


def test_a_rotated_plate_dimensions_meet_the_outline(tmp_path):
    """45 degrees about Z turns the top view into a diamond. 45 about Y does that to the front."""
    document = Document(tmp_path)
    _box(document, "plate", [80, 12, 40])
    _assembly(document, "yaw", [0, 0, 45])
    yaw = document.export_drawing("yaw", scale="1:1")
    yaw_span = 80 * math.cos(math.radians(45)) + 12 * math.sin(math.radians(45))
    sizes = {item["axis"]: item["mm"] for item in yaw["dimensions"]}
    assert sizes["X"] == pytest.approx(yaw_span, abs=0.05)
    assert sizes["Y"] == pytest.approx(yaw_span, abs=0.05)
    assert sizes["Z"] == pytest.approx(40, abs=0.05)
    texts = [entity.dxf.text for entity in ezdxf.readfile(yaw["files"]["dxf"]).modelspace().query("TEXT")]
    assert _format_mm(sizes["X"]) in texts
    assert _format_mm(sizes["Y"]) in texts
    assert _extensions_on_the_outline(yaw["files"]["dxf"]) == 6

    _assembly(document, "pitch", [0, 45, 0])
    pitch = document.export_drawing("pitch", scale="1:2")
    pitch_span = 80 * math.cos(math.radians(45)) + 40 * math.sin(math.radians(45))
    pitch_sizes = {item["axis"]: item["mm"] for item in pitch["dimensions"]}
    assert pitch_sizes["X"] == pytest.approx(pitch_span, abs=0.05)
    assert pitch_sizes["Z"] == pytest.approx(pitch_span, abs=0.05)
    assert pitch_sizes["Y"] == pytest.approx(12, abs=0.05)
    assert _extensions_on_the_outline(pitch["files"]["dxf"]) == 6


def test_two_parts_rotated_on_a_joint_keep_dimensions_on_the_outline(tmp_path):
    document = Document(tmp_path)
    _box(document, "plate", [60, 10, 24])
    document.create_part_from_ops(
        "rib",
        [{"op": "add", "shape": "box", "size": [8, 30, 16], "center": [0, 0, 0]}],
    )
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [
                {"name": "plate", "part": "plate"},
                {"name": "rib", "part": "rib"},
            ],
            "joints": [
                {
                    "name": "hinge",
                    "type": "revolute",
                    "parent": "plate",
                    "child": "rib",
                    "parent_anchor": [30, 0, 0],
                    "child_anchor": [0, -15, 0],
                    "axis": [0, 0, 1],
                    "value": 50,
                }
            ],
        }
    )
    report = document.export_drawing("hinge", scale="1:1")
    assert report["dimensions"]
    assert _extensions_on_the_outline(report["files"]["dxf"]) == 6
    assert Path(report["files"]["pdf"]).is_file()
