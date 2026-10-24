"""Workspace drawing, bill of materials, and exploded-view paths stay in the project.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT. The command line still
writes a built-in export at the path the person named. These tests cover the
workspace commands used by the app and by MCP.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.workspace import Workspace

CANARY_NAME = "SANDBOX_CANARY.txt"


def _stand(workspace: Workspace) -> None:
    document = workspace.document
    document.create_part_from_ops(
        "base",
        [{"op": "add", "shape": "box", "size": [20, 10, 6], "center": [0, 0, 3]}],
    )
    document.create_assembly({"name": "stand", "instances": [{"name": "base", "part": "base"}], "joints": []})


def _canary(tmp_path: Path) -> Path:
    return tmp_path / "outside" / CANARY_NAME


@pytest.mark.parametrize(
    "command",
    ["export_drawing", "bill_of_materials", "exploded_view", "render_animation"],
)
@pytest.mark.parametrize(
    "path_for",
    ["absolute", "relative", "backslash"],
)
def test_workspace_export_paths_cannot_leave_the_project(tmp_path, monkeypatch, command, path_for):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    canary = _canary(tmp_path)
    if path_for == "absolute":
        raw = str(canary)
    elif path_for == "relative":
        raw = "../outside/" + CANARY_NAME
    else:
        raw = "folder\\..\\..\\outside\\" + CANARY_NAME
    extra = {"on_drawing": True} if command == "exploded_view" else {}
    workspace = Workspace(project)
    with pytest.raises(GeometryError, match="outside the project"):
        workspace.execute(command, {"name": "stand", "path": raw, **extra}, source="agent")
    assert not canary.exists()
    assert not (tmp_path / "outside").exists()
    assert list(project.rglob(CANARY_NAME)) == []


def test_a_symlink_export_directory_cannot_leave_the_project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = project / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a symlink")
    workspace = Workspace(project)
    with pytest.raises(GeometryError, match="outside the project"):
        workspace.execute(
            "export_drawing",
            {"name": "stand", "path": "linked"},
            source="agent",
        )
    assert list(outside.iterdir()) == []


def test_an_in_project_export_path_still_writes(tmp_path):
    project = tmp_path / "proj"
    workspace = Workspace(project)
    _stand(workspace)
    drawing = workspace.execute(
        "export_drawing",
        {"name": "stand", "path": "exports/sheets"},
        source="agent",
    )
    pdf = Path(drawing["files"]["pdf"])
    assert pdf.is_file()
    assert pdf.resolve().is_relative_to(project.resolve())
    bom = workspace.execute(
        "bill_of_materials",
        {"name": "stand", "path": "exports/bom/stand.csv"},
        source="ui",
    )
    csv_path = Path(bom["csv"])
    assert csv_path.is_file()
    assert csv_path.resolve().is_relative_to(project.resolve())
    exploded = workspace.execute(
        "exploded_view",
        {"name": "stand", "on_drawing": True, "path": str(project / "exports" / "exploded")},
        source="agent",
    )
    sheet = Path(exploded["drawing"]["pdf"])
    assert sheet.is_file()
    assert sheet.resolve().is_relative_to(project.resolve())
    assert not (tmp_path / "outside" / CANARY_NAME).exists()


def test_the_command_line_document_export_is_unchanged(tmp_path):
    """The person's Document call still accepts a directory inside the project."""
    project = tmp_path / "proj"
    document = Document(project)
    document.create_part_from_ops(
        "base",
        [{"op": "add", "shape": "box", "size": [12, 8, 4], "center": [0, 0, 2]}],
    )
    document.create_assembly({"name": "stand", "instances": [{"name": "base", "part": "base"}], "joints": []})
    report = document.export_drawing("stand", out_dir=project / "sheets")
    assert Path(report["files"]["pdf"]).resolve().is_relative_to(project.resolve())
