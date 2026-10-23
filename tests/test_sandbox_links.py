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
    fake = tmp_path / "fake-slicer"
    fake.write_text(
        "#!/usr/bin/env python3\n"
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

    if not paths.nofollow_writes():
        pytest.skip("this system cannot open a directory without following a link")
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
        link.symlink_to(outside, target_is_directory=True)

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
    step = project / "exports" / "box.step"
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": [4, 4, 4]}]), step)
    assert step.is_file() and step.stat().st_size > 0 and not step.is_symlink()
    assert len(copied) == 2
    assert all(not path.exists() for path in copied)


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
