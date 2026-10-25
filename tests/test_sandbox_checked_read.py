"""A project file is opened from the directory that was checked.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, outside the project.
Replacing a project file with a link to that canary after the check must not
return the canary text.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aicad.blueprint import open_blueprint
from aicad.document import Document
from aicad.errors import AICADError, DocumentError, GeometryError
from aicad.workspace import Workspace

_CANARY_NAME = "SANDBOX_CANARY.txt"
_CANARY_TEXT = "CANARY_HIT\n"
_MAX_SESSION_BYTES = 32 * 1024 * 1024


def _canary(directory: Path) -> Path:
    path = directory / _CANARY_NAME
    path.write_text(_CANARY_TEXT, encoding="utf-8")
    return path


def _link(path: Path, target: Path, *, directory: bool = False) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"this system cannot create a symlink ({exc})")


def _swap_file(root: Path, relative: Path, name: str, target: Path) -> None:
    if Path(relative).name != name:
        return
    current = Path(root) / relative
    if current.is_symlink():
        return
    if current.exists():
        current.unlink()
    _link(current, target)


def _assert_canary(canary: Path, text: str) -> None:
    assert _CANARY_TEXT not in text
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_session_is_read_when_it_stays_a_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    payload = {"units": "mm", "sketches": [], "parts": [], "assemblies": []}
    (project / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    document = Document(project)
    assert document.list_parts() == []


def test_a_swapped_session_is_not_read(tmp_path, monkeypatch):
    from aicad import paths

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text("{}\n", encoding="utf-8")

    def swap(root, relative):
        _swap_file(root, relative, "session.json", canary)

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    with pytest.raises(DocumentError) as caught:
        Document(project)
    _assert_canary(canary, str(caught.value))


def test_a_deep_session_is_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text("[" * 49 + "0" + "]" * 49, encoding="utf-8")
    with pytest.raises(DocumentError, match="nested too deeply"):
        Document(project)


def test_a_huge_session_is_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    path = project / "session.json"
    with path.open("wb") as handle:
        handle.seek(_MAX_SESSION_BYTES)
        handle.write(b"x")
    with pytest.raises(DocumentError, match="too large"):
        Document(project)


def test_a_note_is_read_when_it_stays_a_file(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("write_document", {"name": "notes.md", "markdown": "keep\n"})
    result = workspace.execute("read_document", {"name": "notes.md"})
    assert result["markdown"] == "keep\n"


def test_a_swapped_note_is_not_read(tmp_path, monkeypatch):
    from aicad import paths

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    workspace = Workspace(tmp_path / "project")
    workspace.execute("write_document", {"name": "notes.md", "markdown": "keep\n"})

    def swap(root, relative):
        _swap_file(root, relative, "notes.md", canary)

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    with pytest.raises(GeometryError) as caught:
        workspace.execute("read_document", {"name": "notes.md"})
    _assert_canary(canary, str(caught.value))


def test_a_swapped_requirements_file_is_not_read(tmp_path, monkeypatch):
    from aicad import paths

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.json").write_text('{"requirements": []}\n', encoding="utf-8")

    def swap(root, relative):
        _swap_file(root, relative, "requirements.json", canary)

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    with pytest.raises(AICADError) as caught:
        Workspace(project)
    _assert_canary(canary, str(caught.value))


def test_a_swapped_blueprint_is_not_read(tmp_path, monkeypatch):
    from aicad import paths

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    sheet = project / "sheet.png"
    sheet.write_bytes(b"\x89PNG\r\n\x1a\nhello")

    def swap(root, relative):
        _swap_file(root, relative, "sheet.png", canary)

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    with pytest.raises(AICADError) as caught:
        open_blueprint(sheet, project)
    _assert_canary(canary, str(caught.value))


def test_a_blueprint_in_the_project_is_read(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sheet = project / "sheet.png"
    sheet.write_bytes(b"plain-blueprint")
    opened = open_blueprint(sheet, project)
    assert opened["tool_calls"] == []
    assert "plain-blueprint" in opened["text"]
    assert _CANARY_TEXT not in opened["text"]


def test_a_swapped_step_file_is_not_read(tmp_path, monkeypatch):
    from aicad import paths
    from aicad.kernel import import_step_file

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    step = project / "part.step"
    step.write_text("ISO-10303-21;\n", encoding="ascii")
    paths.remember_project(project)

    def swap(root, relative):
        _swap_file(root, relative, "part.step", canary)

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    with pytest.raises(GeometryError) as caught:
        import_step_file(step)
    _assert_canary(canary, str(caught.value))


def test_a_swapped_record_is_not_read(tmp_path, monkeypatch):
    from aicad import paths
    from aicad.session_record import _load

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    record = project / "records"
    record.mkdir(parents=True)
    (record / "session.json").write_text(
        json.dumps({"format": "session_record.v1", "steps": []}),
        encoding="utf-8",
    )

    def swap(root, relative):
        _swap_file(root, relative, "session.json", canary)

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    with pytest.raises(AICADError) as caught:
        _load(project)
    _assert_canary(canary, str(caught.value))


def test_a_note_with_windows_newlines_is_read_as_lf(tmp_path):
    project = tmp_path / "project"
    workspace = Workspace(project)
    note = project / "documents" / "notes.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_bytes(b"# Note\r\n\r\nKeep this text as data.\r")
    read = workspace.execute("read_document", {"name": "notes.md"}, source="ui")
    assert read["markdown"] == "# Note\n\nKeep this text as data.\n"


def test_a_note_directory_swapped_for_a_link_is_not_read(tmp_path, monkeypatch):
    from aicad import paths

    if not paths.nofollow_writes():
        pytest.skip("this system cannot open a directory without following a link")
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("write_document", {"name": "notes.md", "markdown": "keep\n"})

    def swap(root, relative):
        if not relative.parts or relative.parts[0] != "documents" or len(relative.parts) == 1:
            return
        link = Path(root) / "documents"
        if link.is_symlink() or not link.is_dir():
            return
        shutil.rmtree(link)
        _link(link, outside, directory=True)

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    with pytest.raises(GeometryError) as caught:
        workspace.execute("read_document", {"name": "notes.md"})
    _assert_canary(canary, str(caught.value))
    assert list(outside.iterdir()) == [canary]
