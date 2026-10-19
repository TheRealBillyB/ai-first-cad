"""Feature contribution: buried adds and cuts that miss."""

from pathlib import Path

import pytest

from aicad import Document

ROOT = Path(__file__).resolve().parents[1]


def test_buried_add_is_flagged_and_a_missed_cut_is_flagged(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "part",
        """
feature.add("base", Box(10, 10, 10))
feature.add("buried", Box(4, 4, 4))
feature.add("post", Pos(0, 0, 9) * Box(2, 2, 8))
feature.cut("miss", Pos(30, 0, 0) * Box(2, 2, 2))
feature.cut("hole", Cylinder(1, 20))
part = feature.result
""",
    )
    report = document.feature_report("part")
    assert report.tracked is True
    assert report.warning is None
    by_name = {step.name: step for step in report.steps}
    assert by_name["buried"].redundant is True
    assert by_name["buried"].buried_fraction == pytest.approx(1)
    assert by_name["post"].redundant is False
    assert by_name["post"].buried_fraction < 0.5
    assert by_name["miss"].redundant is True
    assert by_name["miss"].removed_mm3 == pytest.approx(0)
    assert by_name["hole"].redundant is False
    assert by_name["hole"].removed_mm3 > 1
    assert set(report.redundant_features) == {"buried", "miss"}


def test_plain_script_is_not_tracked_and_ops_are(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("plain", "part = Box(2, 2, 2)\n")
    plain = document.feature_report("plain")
    assert plain.tracked is False
    assert plain.steps == []

    document.create_part_from_ops(
        "ops",
        [
            {"op": "add", "name": "block", "shape": "box", "size": [8, 8, 8]},
            {"op": "cut", "name": "hole", "shape": "cylinder", "radius": 1, "height": 20},
        ],
    )
    ops = document.feature_report("ops")
    assert [step.name for step in ops.steps] == ["block", "hole"]
    assert ops.steps[1].removed_mm3 > 0
    assert ops.redundant_features == []


def test_warning_when_part_is_not_the_feature_result(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "odd",
        """
feature.add("base", Box(10, 10, 10))
part = Box(3, 3, 3)
""",
    )
    report = document.feature_report("odd")
    assert report.tracked is True
    assert report.warning is not None


def test_example_ribs_are_flagged_and_gussets_are_not(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "ribs", (ROOT / "examples" / "motor_mount_buried_ribs.py").read_text(encoding="utf-8")
    )
    document.create_part_from_script(
        "gussets", (ROOT / "examples" / "motor_mount.py").read_text(encoding="utf-8")
    )
    before = document.feature_report("ribs")
    after = document.feature_report("gussets")
    ribs = [step for step in before.steps if step.name.startswith("rib_")]
    gussets = [step for step in after.steps if step.name.startswith("gusset_")]
    assert ribs and all(step.redundant and step.buried_fraction > 0.5 for step in ribs)
    assert gussets and all(not step.redundant and step.buried_fraction < 0.5 for step in gussets)
    assert "rib_neg_x" in before.redundant_features
    assert after.redundant_features == []
