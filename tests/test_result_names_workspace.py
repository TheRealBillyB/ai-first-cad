"""Every command result says which project folder it acted on."""

from aicad.workspace import Workspace


def test_a_command_result_names_the_project_folder(tmp_path):
    project = tmp_path / "vise"
    project.mkdir()
    workspace = Workspace(project)
    created = workspace.execute(
        "create_part_from_ops",
        {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [10, 10, 10]}]},
        source="agent",
    )
    assert created["workspace"] == "vise"
    listed = workspace.execute("list_parts", {}, source="agent")
    assert listed["workspace"] == "vise"


def test_undo_and_redo_name_the_project_folder_too(tmp_path):
    project = tmp_path / "clamp"
    project.mkdir()
    workspace = Workspace(project)
    workspace.execute("create_part_from_ops", {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]}, source="agent")
    assert workspace.execute("undo", {}, source="agent")["workspace"] == "clamp"
    assert workspace.execute("redo", {}, source="agent")["workspace"] == "clamp"
