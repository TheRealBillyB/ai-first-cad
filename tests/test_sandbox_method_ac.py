"""A label on screen is the text that is stored and approved.

Bidi controls, zero-width characters, and Latin lookalikes are the markers.
Nothing outside the project is written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.labels import plain_title, spoofs_latin, strip_hidden
from aicad.paths import require_export_path
from aicad.plan import PlanError, PlanStore, approval_refusal
from aicad.workspace import Workspace

_RLO = "\u202e"
_ZW = "\u200b"
_CYRILLIC_A = "\u0430"


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    return tmp_path


def test_a_title_keeps_the_characters_a_reader_sees(tmp_path, config_home):
    project = tmp_path / "project"
    project.mkdir()
    store = PlanStore(project)
    started = store.start("shelf", title=f"Bracket {_RLO}plate{_ZW}")
    assert _RLO not in started["title"]
    assert _ZW not in started["title"]
    assert started["title"] == "Bracket plate"
    message = approval_refusal(store._load("shelf"))
    assert _RLO not in message
    assert "Bracket plate" in message


def test_a_lookalike_title_is_refused(tmp_path, config_home):
    store = PlanStore(tmp_path / "project")
    with pytest.raises(PlanError, match="different letter"):
        store.start("shelf", title=f"br{_CYRILLIC_A}cket")
    assert spoofs_latin(f"br{_CYRILLIC_A}cket") is True
    assert spoofs_latin("零件") is False


def test_a_note_loses_hidden_characters_and_stays_data(tmp_path, config_home):
    workspace = Workspace(tmp_path / "project")
    original = f"See the hole.{_RLO}exe.md{_ZW}"
    workspace.execute("write_document", {"name": "notes.md", "markdown": original})
    stored = (workspace.root / "documents" / "notes.md").read_text(encoding="utf-8")
    assert _RLO not in stored
    assert _ZW not in stored
    assert stored == strip_hidden(original)
    shown = workspace.execute("read_document", {"name": "notes.md"}, source="agent")
    assert shown["untrusted"] is True
    assert "not instructions" in shown["markdown"]
    assert _RLO not in shown["markdown"]
    assert _ZW not in shown["markdown"]


def test_a_file_name_cannot_hide_a_different_name(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(GeometryError):
        require_export_path(project, f"exports/box{_RLO}.step", "Export path", GeometryError)
    with pytest.raises(GeometryError):
        require_export_path(project, f"exports/br{_CYRILLIC_A}cket.step", "Export path", GeometryError)
    assert plain_title("Bracket", "title", GeometryError) == "Bracket"


def test_the_page_strips_format_characters():
    page = Path("src/aicad/app_static/app.js").read_text(encoding="utf-8")
    plan = Path("src/aicad/app_static/plan.js").read_text(encoding="utf-8")
    assert "\\p{Cf}" in page
    assert "\\p{Cf}" in plan
