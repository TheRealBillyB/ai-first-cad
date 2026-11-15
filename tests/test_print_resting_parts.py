"""A part sitting on a slab or on another part is held up, not an overhang.

The print check counts faces near the lowest point of everything checked as
the bed. A full bed slab under a plate of parts moved that point to the
slab's underside, so every part's underside counted as needing support.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.document import Document

_CHECK = {"orientation": "z_up", "render": False, "slice": False}


def _box(document: Document, name: str, size, center) -> None:
    document.create_part_from_ops(name, [{"op": "add", "shape": "box", "size": size, "center": center}])


def test_parts_on_a_bed_slab_need_no_support(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "slab", [100, 100, 2], [0, 0, 1])
    _box(document, "left", [10, 10, 10], [-20, 0, 7])
    _box(document, "right", [12, 8, 6], [20, 10, 5])
    document.create_assembly(
        {
            "name": "plate",
            "instances": [
                {"name": "slab", "part": "slab"},
                {"name": "left", "part": "left"},
                {"name": "right", "part": "right"},
            ],
            "joints": [
                {"name": "a", "type": "fixed", "parent": "slab", "child": "left"},
                {"name": "b", "type": "fixed", "parent": "slab", "child": "right"},
            ],
        }
    )
    report = document.check_print("plate", _CHECK)
    assert report["support_area_mm2"] == pytest.approx(0.0, abs=1e-6)


def test_an_underside_only_partly_on_the_part_below_still_needs_support(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 4], [0, 0, 2])
    _box(document, "lid", [20, 16, 3], [0, 0, 5.5])
    document.create_assembly(
        {
            "name": "stack",
            "instances": [{"name": "base", "part": "base"}, {"name": "lid", "part": "lid"}],
            "joints": [{"name": "sit", "type": "fixed", "parent": "base", "child": "lid"}],
        }
    )
    report = document.check_print("stack", _CHECK)
    # The lid's underside is 20 by 16, and only 10 by 10 of it is on the base. A face
    # only partly held is counted whole, as before, rather than called supported.
    assert report["support_area_mm2"] >= 220


def test_a_part_floating_above_the_slab_still_needs_support(tmp_path: Path):
    document = Document(tmp_path)
    _box(document, "slab", [100, 100, 2], [0, 0, 1])
    _box(document, "block", [10, 10, 10], [0, 0, 8])
    document.create_assembly(
        {
            "name": "plate",
            "instances": [{"name": "slab", "part": "slab"}, {"name": "block", "part": "block"}],
            "joints": [{"name": "a", "type": "fixed", "parent": "slab", "child": "block"}],
        }
    )
    report = document.check_print("plate", _CHECK)
    assert report["support_area_mm2"] == pytest.approx(100, abs=5)
