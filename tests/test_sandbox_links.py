"""Links and parent paths in a shared project stay inside that project.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT. Opening, saving,
exporting, and rendering the project must not create it outside the project,
and must not change one that is already there.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import AICADError
from aicad.sandbox import run_part_script, sandbox_supported
from aicad.workspace import Workspace

_BOX = [{"op": "add", "shape": "box", "size": [4, 4, 4]}]
_CANARY_NAME = "SANDBOX_CANARY.txt"
_CANARY_TEXT = "CANARY_HIT\n"


def _canary(directory: Path) -> Path:
    return directory / _CANARY_NAME


def _assert_absent(path: Path) -> None:
    assert not path.exists()
    assert not path.is_symlink()


def _assert_text(path: Path, text: str) -> None:
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == text


def _empty_session() -> str:
    return json.dumps({"units": "mm", "sketches": [], "assemblies": [], "parts": []}) + "\n"


def _link_dir(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory symlink")


def _try(action) -> None:
    try:
        action()
    except AICADError:
        return


def test_opening_does_not_follow_a_notes_directory_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text(_empty_session(), encoding="utf-8")
    _link_dir(project / "documents", outside)
    _try(lambda: Workspace(project))
    _assert_absent(_canary(outside))
    assert not (outside / "requirements.md").exists()
    assert not (outside / "decisions.md").exists()


def test_opening_does_not_follow_a_requirements_file_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text(_empty_session(), encoding="utf-8")
    (project / "requirements.json").symlink_to(_canary(outside))
    _try(lambda: Workspace(project))
    _assert_absent(_canary(outside))


def test_opening_does_not_replace_a_session_file_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    workspace = Workspace(project)
    session = project / "session.json"
    session.unlink(missing_ok=True)
    session.symlink_to(canary)
    (project / "session.json.tmp").symlink_to(canary)
    _try(lambda: workspace.execute("save_project", {}))
    _assert_text(canary, _CANARY_TEXT)


def test_save_as_does_not_follow_a_directory_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    workspace = Workspace(project)
    documents = project / "documents"
    if documents.is_symlink():
        documents.unlink()
    elif documents.exists():
        shutil.rmtree(documents)
    _link_dir(documents, outside)
    copy = tmp_path / "copy"
    _try(lambda: workspace.execute("save_project_as", {"path": str(copy)}))
    _assert_text(canary, _CANARY_TEXT)
    assert not (copy / "documents" / _CANARY_NAME).exists()
    assert not (outside / "requirements.md").exists()
    plain = Workspace(tmp_path / "plain")
    linked = tmp_path / "linked-copy"
    linked.mkdir()
    _link_dir(linked / "documents", outside)
    _try(lambda: plain.execute("save_project_as", {"path": str(linked)}))
    _assert_text(canary, _CANARY_TEXT)
    assert not (outside / "requirements.md").exists()
    assert not (outside / "decisions.md").exists()


def test_a_note_link_is_not_followed_after_open(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "shared"
    project.mkdir()
    (project / "session.json").write_text(_empty_session(), encoding="utf-8")
    _link_dir(project / "documents", outside)
    workspace = Workspace(tmp_path / "home")
    _try(lambda: workspace.execute("open_project", {"path": str(project)}))
    _try(lambda: workspace.execute("write_document", {"name": "notes.md", "markdown": _CANARY_TEXT}))
    _assert_absent(_canary(outside))
    assert not (outside / "notes.md").exists()


def test_render_does_not_follow_a_directory_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("create_part_from_ops", {"name": "box", "ops": _BOX})
    renders = project / "renders"
    if renders.exists():
        if renders.is_symlink():
            renders.unlink()
        else:
            import shutil

            shutil.rmtree(renders)
    _link_dir(renders, outside)
    _try(lambda: workspace.execute("render_part", {"name": "box"}))
    assert list(outside.rglob("*")) == []
    _assert_absent(_canary(outside))


def test_export_does_not_follow_a_directory_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("create_part_from_ops", {"name": "box", "ops": _BOX})
    _link_dir(project / "exports", outside)
    _try(
        lambda: workspace.execute(
            "export_part",
            {"name": "box", "format": "step", "path": "exports/box.step"},
        )
    )
    assert list(outside.rglob("*")) == []
    _assert_absent(_canary(outside))


def test_export_does_not_follow_a_file_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("create_part_from_ops", {"name": "box", "ops": _BOX})
    (project / "exports").mkdir()
    (project / "exports" / "box.step").symlink_to(_canary(outside))
    _try(
        lambda: workspace.execute(
            "export_part",
            {"name": "box", "format": "step", "path": "exports/box.step"},
        )
    )
    _assert_absent(_canary(outside))


def test_export_does_not_write_through_a_hard_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text("keep\n", encoding="utf-8")
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("create_part_from_ops", {"name": "box", "ops": _BOX})
    (project / "exports").mkdir()
    try:
        os.link(canary, project / "exports" / "box.step")
    except OSError:
        pytest.skip("this machine cannot create a hard link")
    _try(
        lambda: workspace.execute(
            "export_part",
            {"name": "box", "format": "step", "path": "exports/box.step"},
        )
    )
    _assert_text(canary, "keep\n")


def test_export_parent_path_does_not_leave_the_project(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("create_part_from_ops", {"name": "box", "ops": _BOX})
    with pytest.raises(AICADError):
        workspace.execute(
            "export_part",
            {"name": "box", "format": "step", "path": "../outside/SANDBOX_CANARY.txt"},
        )
    _assert_absent(_canary(outside))


def test_import_does_not_follow_a_directory_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    maker = tmp_path / "maker"
    document = Document(maker)
    document.create_part_from_ops("box", _BOX)
    step = maker / "box.step"
    document.export("box", step, "step")
    project = tmp_path / "project"
    project.mkdir()
    _link_dir(project / "imports", outside)
    victim = Document(project)
    _try(lambda: victim.import_step("box", step))
    assert list(outside.rglob("*")) == []
    _assert_absent(_canary(outside))


def test_script_geometry_does_not_follow_a_directory_link(tmp_path):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    workspace = Workspace(project)
    _link_dir(project / ".aicad", outside)
    _try(
        lambda: workspace.execute(
            "create_part_from_script",
            {"name": "box", "script": "part = Box(1, 1, 1)\n"},
        )
    )
    assert list(outside.rglob("*")) == []
    _assert_absent(_canary(outside))


def test_delete_does_not_follow_a_render_directory_link(tmp_path):
    outside = tmp_path / "outside"
    nested = outside / "box"
    nested.mkdir(parents=True)
    canary = nested / _CANARY_NAME
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("create_part_from_ops", {"name": "box", "ops": _BOX})
    _link_dir(project / "renders", outside)
    _try(lambda: workspace.execute("delete_part", {"name": "box"}))
    _assert_text(canary, _CANARY_TEXT)


def test_session_parent_paths_are_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    payload = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {"name": "stolen", "source": "step", "step_file": "../outside/SANDBOX_CANARY.txt"},
            {
                "name": "geom",
                "source": "script",
                "script": "part = Box(1, 1, 1)\n",
                "geometry_file": "foo/../../outside/SANDBOX_CANARY.txt",
                "script_sha256": "0",
            },
            {"name": "win", "source": "step", "step_file": r"..\outside\SANDBOX_CANARY.txt"},
        ],
    }
    (project / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    document = Document(project)
    assert document.list_parts()[0].built is False
    assert all(part.built is False for part in document.list_parts())
    _assert_text(canary, _CANARY_TEXT)


def test_a_zip_in_the_project_is_not_extracted(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text(_empty_session(), encoding="utf-8")
    archive = project / "pack.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../SANDBOX_CANARY.txt", _CANARY_TEXT)
        bundle.writestr("../outside/SANDBOX_CANARY.txt", _CANARY_TEXT)
        bundle.writestr("exports/../../SANDBOX_CANARY.txt", _CANARY_TEXT)
    Document(project)
    Workspace(tmp_path / "home").execute("open_project", {"path": str(project)})
    _assert_absent(tmp_path / _CANARY_NAME)
    _assert_absent(_canary(outside))
    _assert_absent(project / _CANARY_NAME)


def test_a_child_of_a_linked_project_is_not_opened(tmp_path):
    outside = tmp_path / "outside"
    nested = outside / "nested"
    nested.mkdir(parents=True)
    (outside / "session.json").write_text(_empty_session(), encoding="utf-8")
    (nested / "keep.txt").write_text("keep\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text(_empty_session(), encoding="utf-8")
    _link_dir(project / "linked", outside)
    for root in (project, tmp_path / "home"):
        workspace = Workspace(root)
        with pytest.raises(AICADError, match="link outside"):
            workspace.execute("open_project", {"path": str(project / "linked" / "nested")})
        assert workspace.root == root.resolve()
    assert (nested / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert not (nested / "session.json").exists()
    assert not (nested / "requirements.json").exists()
    assert not (outside / "requirements.json").exists()


def test_a_link_that_stays_inside_the_project_can_be_opened(tmp_path):
    project = tmp_path / "project"
    inner = project / "inner"
    inner.mkdir(parents=True)
    (project / "session.json").write_text(_empty_session(), encoding="utf-8")
    (inner / "session.json").write_text(_empty_session(), encoding="utf-8")
    _link_dir(project / "alias", inner)
    workspace = Workspace(tmp_path / "home")
    workspace.execute("open_project", {"path": str(project / "alias")})
    assert workspace.root == inner.resolve()


def test_a_linked_subfolder_is_not_opened(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text(_empty_session(), encoding="utf-8")
    _link_dir(project / "linked", outside)
    workspace = Workspace(tmp_path / "home")
    listed = workspace.execute("list_folders", {"path": str(project)})
    assert all(item["name"] != "linked" for item in listed["folders"])
    with pytest.raises(AICADError):
        workspace.execute("open_project", {"path": str(project / "linked")})
    assert workspace.root == (tmp_path / "home").resolve()
    _assert_absent(_canary(outside))
    assert not (outside / "requirements.json").exists()
    assert not (outside / "session.json").exists()


def test_a_slicer_does_not_write_through_a_link(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text("keep\n", encoding="utf-8")
    renders = tmp_path / "project" / "renders"
    renders.mkdir(parents=True)
    gcode = renders / "print.gcode"
    try:
        os.link(canary, gcode)
    except OSError:
        pytest.skip("this machine cannot create a hard link")
    script = tmp_path / "fake_slicer.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if '--output' in args:\n"
        "    dest = args[args.index('--output') + 1]\n"
        "else:\n"
        "    dest = args[args.index('--export-gcode') + 1]\n"
        "Path(dest).write_text('CANARY_HIT\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        fake = tmp_path / "fake-slicer.cmd"
        fake.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        real_run = subprocess.run

        def run(command, **kwargs):
            if command and command[0] == str(fake):
                command = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command[0], *command[1:]]
            return real_run(command, **kwargs)

        monkeypatch.setattr("aicad.printprep.subprocess.run", run)
    else:
        fake = tmp_path / "fake-slicer"
        fake.write_text("#!/usr/bin/env python3\n" + script.read_text(encoding="utf-8"), encoding="utf-8")
        fake.chmod(0o755)
    monkeypatch.setattr("aicad.printprep.find_slicers", lambda: [str(fake)])
    import numpy as np

    from aicad.printprep import slice_mesh

    vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    triangles = np.array([[0, 1, 2]])
    slice_mesh(vertices, triangles, renders)
    _assert_text(canary, "keep\n")
    gcode.unlink(missing_ok=True)
    gcode.symlink_to(canary)
    slice_mesh(vertices, triangles, renders)
    _assert_text(canary, "keep\n")


def test_a_swapped_directory_is_not_written(tmp_path, monkeypatch):
    from aicad import paths

    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "exports").mkdir()
    workspace = Workspace(project)

    def swap(root, relative):
        if not relative.parts:
            return
        link = Path(root) / relative.parts[0]
        if link.is_symlink() or not link.is_dir():
            return
        shutil.rmtree(link)
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if sys.platform != "win32":
                raise
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise OSError(completed.stderr.strip() or "could not create a junction")

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    monkeypatch.setattr(paths, "_reopen_accepts_create", lambda _path: False)
    with pytest.raises(OSError):
        paths.publish_text(project / "exports" / "part.txt", _CANARY_TEXT, root=project)
    assert list(outside.iterdir()) == []
    _assert_absent(outside / "part.txt")
    with pytest.raises(AICADError):
        workspace.execute("write_document", {"name": "notes.md", "markdown": _CANARY_TEXT})
    assert list(outside.iterdir()) == []
    _assert_absent(outside / "notes.md")
    _assert_absent(_canary(outside))


def test_a_private_file_is_copied_into_the_project(tmp_path, monkeypatch):
    from aicad import paths
    from aicad.kernel import export_step_file, make_from_ops

    if not paths.nofollow_writes():
        pytest.skip("this system cannot open a directory without following a link")
    copied: list[Path] = []
    real_copy = paths._copy_into_descriptor

    def copy(descriptor: int, source: Path) -> None:
        copied.append(source)
        real_copy(descriptor, source)

    monkeypatch.setattr(paths, "_reopen_accepts_create", lambda _path: False)
    monkeypatch.setattr(paths, "_copy_into_descriptor", copy)
    project = tmp_path / "project"
    project.mkdir()
    paths.remember_project(project)
    target = project / "exports" / "part.txt"
    paths.publish_text(target, "inside\n", root=project)
    assert target.read_text(encoding="utf-8") == "inside\n"
    assert target.is_file() and not target.is_symlink()
    assert target.stat().st_mode & 0o400
    exports = project / "exports"
    assert exports.is_dir() and not exports.is_symlink()
    assert exports.stat().st_mode & 0o700 == 0o700
    step = project / "exports" / "box.step"
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": [4, 4, 4]}]), step)
    assert step.is_file() and step.stat().st_size > 0 and not step.is_symlink()
    assert len(copied) == 2
    assert all(not path.exists() for path in copied)


def test_a_locked_directory_refuses_the_new_file(tmp_path, monkeypatch):
    from aicad import paths

    monkeypatch.setattr(paths, "nofollow_writes", lambda: False)
    locked = tmp_path / "locked"
    locked.mkdir()
    paths.deny_directory_write(locked)
    try:
        with pytest.raises(OSError):
            paths.publish_text(locked / "part.txt", "inside\n")
    finally:
        paths.allow_directory_write(locked)
    assert list(locked.iterdir()) == []


def test_a_part_script_does_not_use_a_hard_link(tmp_path):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text("keep\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    try:
        os.link(canary, project / "alias.txt")
    except OSError:
        pytest.skip("this machine cannot create a hard link")
    script = "from pathlib import Path\nPath('alias.txt').write_text('CANARY_HIT\\n', encoding='utf-8')\npart = Box(1, 1, 1)\n"
    _try(lambda: run_part_script(script, project))
    _assert_text(canary, "keep\n")


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows directory link")
def test_export_does_not_follow_a_junction(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("create_part_from_ops", {"name": "box", "ops": _BOX})
    link = project / "exports"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("this machine cannot create a junction")
    _try(
        lambda: workspace.execute(
            "export_part",
            {"name": "box", "format": "step", "path": "exports/box.step"},
        )
    )
    assert list(outside.rglob("*")) == []
    _assert_absent(_canary(outside))


def test_a_directory_is_not_created_through_a_link_that_stays_inside(tmp_path, monkeypatch):
    from aicad.errors import GeometryError
    from aicad.paths import output_dir, output_path

    root = tmp_path / "project"
    kept = root / "kept"
    kept.mkdir(parents=True)
    link = root / "alias"
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(kept)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("this machine cannot create a junction")
    else:
        try:
            link.symlink_to(kept, target_is_directory=True)
        except OSError:
            pytest.skip("this machine cannot create a directory symlink")
        monkeypatch.setattr("aicad.paths.nofollow_writes", lambda: False)
    with pytest.raises(GeometryError, match="link and was not followed"):
        output_dir(root, link / "new-folder", "Render", GeometryError)
    with pytest.raises(GeometryError, match="link and was not followed"):
        output_path(root, link / "new-folder" / "part.step", "Export", GeometryError)
    assert not (kept / "new-folder").exists()
    made = output_dir(root, root / "renders" / "box", "Render", GeometryError)
    assert made.is_dir()
    assert (root / "renders" / "box").is_dir()
    assert not (kept / "renders").exists()


def _directory_link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("this machine cannot create a junction")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory symlink")


def _hard_link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(target, link)
    except OSError:
        pytest.skip("this machine cannot create a hard link")


def _regular_text(root: Path) -> str:
    """Text of ordinary files under ``root``. A link is not read."""
    if not root.exists() or root.is_symlink():
        return ""
    chunks: list[bytes] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        chunks.append(path.read_bytes())
    return b"".join(chunks).decode("utf-8", errors="replace")


def test_save_as_does_not_follow_a_link_that_stays_inside(tmp_path):
    from aicad.errors import GeometryError

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    workspace = Workspace(project)
    hold = project / "hold"
    hold.mkdir()
    _hard_link(hold / "alias.txt", canary)
    _directory_link(project / "documents" / "pocket", hold)
    copy = tmp_path / "copy"
    with pytest.raises(GeometryError, match="link") as caught:
        workspace.execute("save_project_as", {"path": str(copy)})
    assert _CANARY_TEXT not in str(caught.value)
    _assert_text(canary, _CANARY_TEXT)
    assert _CANARY_TEXT not in _regular_text(copy)
    assert list(outside.iterdir()) == [canary]
    assert workspace.root == project.resolve()


def test_save_as_does_not_copy_a_file_with_another_name(tmp_path):
    from aicad.errors import GeometryError

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    workspace = Workspace(project)
    _hard_link(project / "documents" / "alias.txt", canary)
    copy = tmp_path / "copy"
    with pytest.raises(GeometryError, match="another name") as caught:
        workspace.execute("save_project_as", {"path": str(copy)})
    assert _CANARY_TEXT not in str(caught.value)
    _assert_text(canary, _CANARY_TEXT)
    assert _CANARY_TEXT not in _regular_text(copy)
    assert list(outside.iterdir()) == [canary]
    assert workspace.root == project.resolve()


def test_save_as_does_not_read_a_file_swapped_for_a_link(tmp_path, monkeypatch):
    from aicad import paths
    from aicad.errors import GeometryError

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    workspace = Workspace(project)
    notes = project / "documents"
    notes.mkdir(exist_ok=True)
    (notes / "notes.md").write_text("keep\n", encoding="utf-8")

    def swap(root, relative) -> None:
        if Path(relative).name != "notes.md":
            return
        current = Path(root) / relative
        if current.is_symlink() or not current.is_file():
            return
        current.unlink()
        try:
            current.symlink_to(canary)
        except OSError:
            pytest.skip("this machine cannot create a symlink")

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    copy = tmp_path / "copy"
    with pytest.raises(GeometryError) as caught:
        workspace.execute("save_project_as", {"path": str(copy)})
    assert _CANARY_TEXT not in str(caught.value)
    _assert_text(canary, _CANARY_TEXT)
    assert _CANARY_TEXT not in _regular_text(tmp_path / "copy")
    assert workspace.root == project.resolve()


def test_a_link_that_leaves_the_project_is_still_outside(tmp_path, monkeypatch):
    from aicad.errors import GeometryError
    from aicad.paths import output_dir, output_path

    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "alias"
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("this machine cannot create a junction")
    else:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("this machine cannot create a directory symlink")
        monkeypatch.setattr("aicad.paths.nofollow_writes", lambda: False)
    with pytest.raises(GeometryError, match="outside the project"):
        output_dir(root, link / "new-folder", "Render", GeometryError)
    with pytest.raises(GeometryError, match="outside the project"):
        output_path(root, link / "new-folder" / "part.step", "Export", GeometryError)
    assert list(outside.iterdir()) == []


def test_a_file_written_through_a_project_link_stays_in_the_project(tmp_path):
    from aicad.paths import publish_text

    outside = tmp_path / "outside"
    nested = outside / "sub"
    nested.mkdir(parents=True)
    canary = nested / _CANARY_NAME
    canary.write_bytes(b"keep\n")
    real = tmp_path / "real"
    real.mkdir()
    (real / "session.json").write_text("{}\n", encoding="utf-8")
    _directory_link(real / "docs", outside)
    link = tmp_path / "link"
    _directory_link(link, real)
    publish_text(link / "notes" / "part.txt", "inside\n")
    assert (real / "notes" / "part.txt").read_text(encoding="utf-8") == "inside\n"
    assert (real / "notes" / "part.txt").is_file()
    assert not (real / "notes" / "part.txt").is_symlink()
    with pytest.raises(OSError):
        publish_text(link / "docs" / "sub" / "part.txt", _CANARY_TEXT)
    assert canary.read_bytes() == b"keep\n"
    assert not (outside / "part.txt").exists()
    assert not (nested / "part.txt").exists()


def test_a_directory_under_a_project_link_is_created_in_the_project(tmp_path):
    from aicad.paths import ensure_real_directory

    outside = tmp_path / "outside"
    outside.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    (real / "session.json").write_text("{}\n", encoding="utf-8")
    _directory_link(real / "docs", outside)
    link = tmp_path / "link"
    _directory_link(link, real)
    ensure_real_directory(link / "exports")
    assert (real / "exports").is_dir()
    assert not (real / "exports").is_symlink()
    with pytest.raises(OSError):
        ensure_real_directory(link / "docs" / "more")
    assert not (outside / "more").exists()


def test_a_directory_is_not_created_through_a_link_outside_a_project(tmp_path):
    from aicad.paths import ensure_real_directory, publish_text

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    _directory_link(link, outside)
    with pytest.raises(OSError):
        ensure_real_directory(link / "exports")
    assert not (outside / "exports").exists()
    with pytest.raises(OSError):
        publish_text(link / "part.txt", "inside\n")
    assert not (outside / "part.txt").exists()


def test_a_new_file_outside_a_project_is_created_from_its_directory(tmp_path):
    from aicad.paths import publish_text

    target = tmp_path / "fresh" / "part.txt"
    publish_text(target, "inside\n")
    assert target.read_text(encoding="utf-8") == "inside\n"
    assert target.is_file() and not target.is_symlink()


def test_a_directory_under_an_earlier_link_is_created_in_the_real_folder(tmp_path):
    from aicad.paths import ensure_real_directory, publish_text

    private = tmp_path / "private" / "var" / "folders"
    private.mkdir(parents=True)
    var = tmp_path / "var"
    _directory_link(var, tmp_path / "private" / "var")
    made = ensure_real_directory(var / "folders" / "job" / "out")
    assert made == Path(os.path.abspath(var / "folders" / "job" / "out"))
    assert (private / "job" / "out").is_dir()
    assert not (private / "job" / "out").is_symlink()
    publish_text(var / "folders" / "job" / "part.txt", "inside\n")
    assert (private / "job" / "part.txt").read_text(encoding="utf-8") == "inside\n"
    assert not (private / "job" / "part.txt").is_symlink()


def test_a_project_link_under_an_earlier_link_is_created_in_the_project(tmp_path):
    from aicad.paths import ensure_real_directory, publish_text

    private = tmp_path / "private" / "var"
    private.mkdir(parents=True)
    var = tmp_path / "var"
    _directory_link(var, private)
    real = private / "real"
    real.mkdir()
    (real / "session.json").write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / _CANARY_NAME).write_bytes(b"keep\n")
    _directory_link(real / "docs", outside)
    link = private / "link"
    _directory_link(link, real)
    ensure_real_directory(var / "link" / "exports")
    assert (real / "exports").is_dir()
    assert not (real / "exports").is_symlink()
    publish_text(var / "link" / "part.txt", "inside\n")
    assert (real / "part.txt").read_text(encoding="utf-8") == "inside\n"
    publish_text(var / "link" / "notes" / "part.txt", "inside\n")
    assert (real / "notes" / "part.txt").read_text(encoding="utf-8") == "inside\n"
    with pytest.raises(OSError):
        ensure_real_directory(var / "link" / "docs" / "more")
    with pytest.raises(OSError):
        publish_text(var / "link" / "docs" / "sub" / "part.txt", _CANARY_TEXT)
    assert not (outside / "more").exists()
    assert (outside / _CANARY_NAME).read_bytes() == b"keep\n"
    assert not (outside / "part.txt").exists()
