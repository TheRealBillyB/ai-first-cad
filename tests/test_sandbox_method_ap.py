"""Text that is shown or stored does not carry a terminal sequence.

The marker is the word CANARY_HIT inside ordinary text. A file outside the
project is not changed. Nothing is sent to another machine.
"""

from __future__ import annotations

import pytest

from aicad.animation import _encoder_reason
from aicad.cli import main
from aicad.errors import GeometryError
from aicad.fea import _ccx_error
from aicad.labels import plain_line, strip_hidden
from aicad.paths import require_export_path
from aicad.plugins import API_VERSION, PluginOffer, _validate_manifest, warning_text
from aicad.workspace import Workspace

_OSC = "\x1b]8;;http://127.0.0.1/CANARY_HIT\x1b\\"
_CSI = "\x1b[2J"
_C1 = "\x9b31m"


def _canary(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text("untouched\n", encoding="utf-8")
    return marker


def test_hidden_sequences_are_dropped_and_a_line_break_in_a_note_stays():
    assert strip_hidden(f"{_OSC}label") == "label"
    assert strip_hidden(f"before{_CSI}after") == "beforeafter"
    assert strip_hidden(f"before{_C1}after") == "beforeafter"
    assert strip_hidden("hole\udcff") == "hole"
    assert strip_hidden("keep\n\tthis") == "keep\n\tthis"
    assert plain_line("Counts.\nCANARY_HIT\x1b[31m") == "Counts. CANARY_HIT"
    assert "\x1b" not in plain_line(f"{_OSC}label")


def test_a_path_and_an_error_do_not_show_the_sequence(tmp_path):
    marker = _canary(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(GeometryError) as raised:
        require_export_path(project, f"exports/box{_CSI}\udcff.step", "Export path", GeometryError)
    shown = str(raised.value)
    assert "\x1b" not in shown
    assert "\udcff" not in shown
    shown.encode("utf-8")
    text = str(GeometryError(f"stopped {_CSI}CANARY_HIT"))
    assert "CANARY_HIT" in text
    assert "\x1b" not in text
    text.encode("utf-8")
    assert marker.read_text(encoding="utf-8") == "untouched\n"


def test_a_plugin_warning_is_one_plain_block(tmp_path):
    marker = _canary(tmp_path)
    offer = PluginOffer(
        name="volume-note",
        distribution=f"demo{_CSI}",
        version="1\r\n0",
        publisher=f"Pub{_OSC}lisher",
        summary="Counts.\nCANARY_HIT\x1b[31m",
        permissions=("checks",),
        provides=("analysis",),
        third_party=True,
        stamp="abc",
        entry="volume_note",
    )
    text = warning_text(offer)
    assert "not a sandbox" in text
    assert "CANARY_HIT" in text
    assert "\x1b" not in text
    assert "\r" not in text
    assert "http://127.0.0.1" not in text
    assert "lisher" in text
    problem, fields = _validate_manifest(
        {
            "api": API_VERSION,
            "name": "volume-note",
            "publisher": _CSI,
            "summary": "Counts.",
            "permissions": ["checks"],
            "provides": ["analysis"],
        },
        "volume-note",
    )
    assert fields is None
    assert problem
    _, fields = _validate_manifest(
        {
            "api": API_VERSION,
            "name": "volume-note",
            "publisher": "A publisher",
            "summary": "Counts.\nCANARY_HIT\x1b[31m",
            "permissions": ["checks"],
            "provides": ["analysis"],
        },
        "volume-note",
    )
    assert fields is not None
    assert fields["summary"] == "Counts. CANARY_HIT"
    assert "\x1b" not in fields["summary"]
    assert marker.read_text(encoding="utf-8") == "untouched\n"


def test_a_command_error_does_not_send_a_terminal_sequence(capsys, monkeypatch):
    def boom(_args):
        raise GeometryError(f"stopped {_CSI}CANARY_HIT")

    monkeypatch.setattr("aicad.cli._dispatch", boom)
    assert main(["plugins", "list"]) == 1
    error = capsys.readouterr().err
    assert "CANARY_HIT" in error
    assert "\x1b" not in error
    error.encode("utf-8")


def test_solver_and_encoder_text_are_one_line():
    detail = _ccx_error(f"start\n*ERROR {_CSI}CANARY_HIT\nend")
    assert "CANARY_HIT" in detail
    assert "\x1b" not in detail
    assert "\n" not in detail
    reason = _encoder_reason(f"fail {_OSC}CANARY_HIT\nnext".encode(), 1)
    assert reason == "fail CANARY_HIT next"
    assert "\x1b" not in reason


def test_a_note_stores_the_visible_text(tmp_path):
    marker = _canary(tmp_path)
    workspace = Workspace(tmp_path / "project")
    original = f"See the hole.{_CSI}\nCANARY_HIT{_OSC}"
    workspace.execute("write_document", {"name": "notes.md", "markdown": original})
    stored = (workspace.root / "documents" / "notes.md").read_text(encoding="utf-8")
    assert stored == strip_hidden(original)
    assert "\x1b" not in stored
    assert "CANARY_HIT" in stored
    assert "\n" in stored
    assert marker.read_text(encoding="utf-8") == "untouched\n"
