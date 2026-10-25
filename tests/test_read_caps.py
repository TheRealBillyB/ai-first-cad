"""A STEP text scan and a copied session stop at their size caps."""

from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.kernel import MAX_STEP_BYTES, _refuse_step_file_outside_folder, _step_code_and_strings
from aicad.workspace import Workspace


def test_the_step_text_scan_stops_at_the_file_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("aicad.kernel.MAX_STEP_BYTES", 64)
    path = tmp_path / "wide.step"
    data = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n" + b" " * 80
    assert len(data) > 64
    assert MAX_STEP_BYTES > 64
    with pytest.raises(GeometryError, match="too large"):
        _refuse_step_file_outside_folder(path, data)


def test_adjacent_step_strings_stay_one_value():
    _code, values = _step_code_and_strings("FILE_NAME('ab' 'cd');")
    assert values == ["abcd"]


def test_save_as_does_not_copy_a_session_past_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    source = tmp_path / "project"
    workspace = Workspace(source)
    session = source / "session.json"
    session.write_bytes(b'{"units": "mm"}' + b" " * 80)
    monkeypatch.setattr("aicad.document._MAX_SESSION_BYTES", 64)
    destination = tmp_path / "copy"
    with pytest.raises(GeometryError, match="too large"):
        workspace.execute("save_project_as", {"path": str(destination)}, source="ui")
    assert not (destination / "session.json").exists()
    assert workspace.root == source.resolve()
    assert Path(session).stat().st_size > 64
