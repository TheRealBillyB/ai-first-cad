"""A motion list is accepted. Anything else is a clear error."""

import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.kinematics import normalize


def _spec(**extra):
    spec = {
        "name": "press",
        "instances": [{"name": "base", "part": "block"}, {"name": "slider", "part": "block"}],
        "joints": [
            {
                "name": "slide",
                "type": "prismatic",
                "parent": "base",
                "child": "slider",
                "parent_anchor": [20, 0, 0],
                "axis": [1, 0, 0],
            }
        ],
    }
    spec.update(extra)
    return spec


def test_a_list_of_named_motions_is_stored_and_runs(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "block",
        [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 0]}],
    )
    document.create_assembly(
        _spec(
            motions=[
                {
                    "name": "advance",
                    "sweep": {"joint": "slide", "start": 0, "end": 10, "frames": 3},
                    "duration_s": 1,
                }
            ]
        )
    )
    stored = document.get_assembly("press")["assembly"]["motions"]
    assert list(stored) == ["advance"]
    assert stored["advance"]["name"] == "advance"
    report = document.run_motion("press", sequence="advance", render=False)
    assert report["motion"] == "advance"
    assert [frame["joints"]["slide"] for frame in report["frames"]] == pytest.approx([0, 5, 10])


def test_a_motion_that_is_not_a_mapping_or_a_named_list_is_an_error():
    with pytest.raises(GeometryError, match="motions must map a name"):
        normalize(_spec(motions="advance"))
    with pytest.raises(GeometryError, match="motions must map a name"):
        normalize(_spec(motions=["advance"]))
    with pytest.raises(GeometryError, match="needs a name"):
        normalize(_spec(motions=[{"sweep": {"joint": "slide", "start": 0, "end": 1, "frames": 2}}]))
    with pytest.raises(GeometryError, match="Two motions are named 'advance'"):
        normalize(
            _spec(
                motions=[
                    {"name": "advance", "sweep": {"joint": "slide", "start": 0, "end": 1, "frames": 2}},
                    {"name": "advance", "sweep": {"joint": "slide", "start": 0, "end": 2, "frames": 2}},
                ]
            )
        )
    kept = normalize(
        _spec(motions={"advance": {"sweep": {"joint": "slide", "start": 0, "end": 4, "frames": 2}}})
    )
    assert kept["motions"]["advance"]["name"] == "advance"
