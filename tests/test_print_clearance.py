"""Print-in-place clearance for an assembly, including joint pairs."""

import json
from pathlib import Path

import pytest

from aicad.cli import main as cli_main
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.workspace import Workspace


def _coupon(document: Document, pin_radius: float, name: str = "coupon") -> None:
    # A 3 mm pin in a bore. The radial gap is (bore radius - pin radius).
    document.create_part_from_ops(
        "plate",
        [
            {"op": "add", "shape": "box", "size": [20, 16, 8], "center": [0, 0, 4]},
            {"op": "cut", "shape": "cylinder", "radius": 3.2, "height": 12, "center": [0, 0, 4]},
        ],
    )
    document.create_part_from_ops(
        "pin",
        [{"op": "add", "shape": "cylinder", "radius": pin_radius, "height": 6, "center": [0, 0, 4]}],
    )
    document.create_assembly(
        {
            "name": name,
            "instances": [{"name": "plate", "part": "plate"}, {"name": "pin", "part": "pin"}],
            "joints": [
                {
                    "name": "spin",
                    "type": "revolute",
                    "parent": "plate",
                    "child": "pin",
                    "axis": [0, 0, 1],
                    "min": 0,
                    "max": 90,
                }
            ],
            "poses": {"quarter": {"spin": 90}},
        }
    )


def test_a_running_gap_passes_and_a_fused_pin_fails(tmp_path: Path):
    document = Document(tmp_path)
    _coupon(document, 3.0)
    report = document.check_print_clearance("coupon")
    assert report["result_kind"] == "verified_check"
    assert report["status"] == "pass"
    assert report["fused_count"] == 0
    pair = report["pairs"][0]
    assert pair["joint"] == "spin"
    assert pair["gap_mm"] == pytest.approx(0.2, abs=0.02)
    assert pair["status"] == "clear"
    assert pair["mesh_closes"] is False

    tight = document.check_print_clearance("coupon", {"min_clearance_mm": 0.3})
    assert tight["status"] == "review"
    assert tight["pairs"][0]["status"] == "tight"

    document.create_part_from_ops(
        "fat",
        [{"op": "add", "shape": "cylinder", "radius": 3.3, "height": 6, "center": [0, 0, 4]}],
    )
    document.create_assembly(
        {
            "name": "fused",
            "instances": [{"name": "plate", "part": "plate"}, {"name": "pin", "part": "fat"}],
            "joints": [
                {
                    "name": "spin",
                    "type": "revolute",
                    "parent": "plate",
                    "child": "pin",
                    "axis": [0, 0, 1],
                }
            ],
        }
    )
    fused = document.check_print_clearance("fused")
    assert fused["status"] == "fail"
    assert fused["pairs"][0]["status"] == "fused"
    assert fused["pairs"][0]["overlap_mm3"] > 0


def test_a_gap_the_mesh_can_close_is_fused(tmp_path: Path):
    document = Document(tmp_path)
    _coupon(document, 3.12)
    # Radial gap is 0.08 mm. The default fuse line is 0.15 mm.
    report = document.check_print_clearance("coupon", {"fuse_mm": 0.05, "min_clearance_mm": 0.05, "mesh_deflection_mm": 0.05})
    assert report["pairs"][0]["gap_mm"] == pytest.approx(0.08, abs=0.02)
    assert report["pairs"][0]["mesh_closes"] is True
    assert report["status"] == "fail"


def test_a_pose_is_checked_without_storing_it(tmp_path: Path):
    document = Document(tmp_path)
    _coupon(document, 3.0)
    report = document.check_print_clearance("coupon", {"pose": "quarter"})
    assert report["status"] == "pass"
    assert report["pose"] == "quarter"
    stored = document.get_assembly("coupon")["assembly"]
    assert stored["joints"][0]["value"] == pytest.approx(0)


def test_workspace_and_cli_report_the_same_gap(tmp_path: Path, capsys):
    document = Document(tmp_path)
    _coupon(document, 3.0)
    workspace = Workspace(tmp_path)
    again = workspace.execute("check_print_clearance", {"name": "coupon"})
    assert again["status"] == "pass"
    assert again["pairs"][0]["gap_mm"] == pytest.approx(0.2, abs=0.02)
    code = cli_main(["print-clearance", "coupon", "--workspace", str(tmp_path), "--min", "0.3"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "review"
    assert payload["result_label"] == "Verified check"


def test_unknown_fields_and_a_reversed_fuse_line_are_rejected(tmp_path: Path):
    document = Document(tmp_path)
    _coupon(document, 3.0)
    with pytest.raises(GeometryError, match="unknown fields"):
        document.check_print_clearance("coupon", {"nozzle": 0.4})
    with pytest.raises(GeometryError, match="fuse_mm"):
        document.check_print_clearance("coupon", {"fuse_mm": 0.4, "min_clearance_mm": 0.2})
