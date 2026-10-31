"""A free-text note stays on one line in a report, a drawing, and a generated file.

The marker is CANARY_HIT. A line separator does not open another line, and
that marker stays where it was written.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from aicad.drawing import _Text, _write_pdf
from aicad.errors import GeometryError, PlanError
from aicad.honesty import sheet_lines
from aicad.plan import PlanStore
from aicad.plan_sheet import plan_sheet_png
from aicad.sketch import Sketch, solve_sketch

_MARKER = "CANARY_HIT"
_LINE = "\u2028"
_PARA = "\u2029"


def _rectangle(name: str) -> Sketch:
    return (
        Sketch()
        .point("origin", 0, 0)
        .fix("origin")
        .point("sw", -36, -26)
        .point("se", 36, -26)
        .point("ne", 36, 26)
        .point("nw", -36, 26)
        .line("bottom", "sw", "se")
        .line("right", "se", "ne")
        .line("top", "ne", "nw")
        .line("left", "nw", "sw")
        .horizontal("bottom")
        .horizontal("top")
        .vertical("left")
        .vertical("right")
        .distance("sw", "se", 72, name=name)
        .distance("sw", "nw", 52, name="base_width")
    )


def _panel() -> dict:
    return {"title": "Top", "blocks": [{"x": 0, "y": 0, "w": 40, "h": 20, "label": "plate"}]}


def test_a_note_with_a_line_separator_is_refused(tmp_path):
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    store = PlanStore(tmp_path)
    store.start("shelf", "Shelf")
    for note in (f"depth{_LINE}{_MARKER}", f"depth{_PARA}next", "depth\rnext", "depth\x1b[31mnext"):
        with pytest.raises(PlanError, match="one line"):
            store.add_research("shelf", note, "shop note")
    with pytest.raises(PlanError, match="one line"):
        store.put_sheet(
            "shelf",
            {"id": "bad", "title": "Top", "panels": [{"blocks": [{"x": 0, "y": 0, "w": 10, "h": 10, "label": f"shelf{_LINE}18 mm"}]}]},
        )
    store.put_sheet(
        "shelf",
        {"id": "top", "title": "Top", "panels": [{"blocks": [{"x": 0, "y": 0, "w": 10, "h": 10, "label": "shelf\n18 mm"}]}]},
    )
    store.add_research("shelf", "18 mm is the usual sheet.", "shop note")
    saved = (tmp_path / "plans" / "shelf.json").read_text(encoding="utf-8")
    assert _LINE not in saved
    assert _PARA not in saved
    assert _MARKER not in saved
    with pytest.raises(GeometryError, match="one line"):
        solve_sketch(_rectangle(f"base{_LINE}length"), name="plate")
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"


def test_a_stored_note_does_not_add_a_line(tmp_path):
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    store = PlanStore(tmp_path)
    store.start("shelf", "Shelf")
    store.add_research("shelf", "18 mm is the usual sheet.", "shop note")
    store.put_sheet("shelf", {"id": "top", "title": "Top", "panels": [_panel()]})
    path = tmp_path / "plans" / "shelf.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["revisions"][0]["research"][0]["note"] = f"18 mm{_LINE}{_MARKER}"
    payload["revisions"][0]["sheets"][0]["footer"] = f"review{_LINE}{_MARKER}"
    payload["revisions"][0]["activity"][0]["text"] = f"note{_PARA}{_MARKER}"
    path.write_text(json.dumps(payload), encoding="utf-8")
    opened = store.get("shelf")
    shown = json.dumps(opened)
    assert _LINE not in shown
    assert _PARA not in shown
    assert "\n" not in opened["revisions"][0]["research"][0]["note"]
    lines = sheet_lines(
        {
            "result_kind": "rough_estimate",
            "result_label": "Rough estimate",
            "result_what": f"A note{_LINE}next",
            "assumptions": [f"depth{_PARA}next"],
            "note": f"log{_LINE}next",
        }
    )
    assert lines
    assert all(_LINE not in line and _PARA not in line and "\n" not in line for line in lines)
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"


def test_a_drawing_keeps_a_note_on_one_line(tmp_path):
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    plain = tmp_path / "plain.pdf"
    nasty = tmp_path / "note.pdf"
    _write_pdf(plain, [], [_Text(10, 10, "review note", 3.0, "TITLE", (0, 0, 0))], [])
    _write_pdf(
        nasty,
        [],
        [_Text(10, 10, f"review{_LINE}note{_PARA}{_MARKER}", 3.0, "TITLE", (0, 0, 0))],
        [],
    )
    assert len(plain.read_bytes().splitlines()) == len(nasty.read_bytes().splitlines())
    text = nasty.read_bytes().decode("latin-1", errors="replace")
    assert _LINE not in text
    assert _PARA not in text
    assert "\nreview" not in text
    clean = plan_sheet_png(title="Shelf", revision=1, status="draft", panels=[_panel()], footer="Review note")
    broken = plan_sheet_png(
        title=f"Shelf{_LINE}next",
        revision=1,
        status="draft",
        panels=[
            {
                "title": f"Top{_LINE}next",
                "notes": [f"depth{_PARA}{_MARKER}"],
                "blocks": [{"x": 0, "y": 0, "w": 40, "h": 20, "label": f"plate{_LINE}{_MARKER}"}],
            }
        ],
        footer=f"review{_LINE}{_MARKER}",
    )
    assert Image.open(__import__("io").BytesIO(clean)).size == Image.open(__import__("io").BytesIO(broken)).size
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
