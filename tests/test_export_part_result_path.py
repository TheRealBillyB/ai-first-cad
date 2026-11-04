"""export_part reports a project path and refuses an empty one."""

from __future__ import annotations

import pytest

from aicad.errors import GeometryError
from aicad.workspace import Workspace

_BOX = [{"op": "add", "shape": "box", "size": [2, 2, 2]}]


def _workspace(tmp_path):
    workspace = Workspace(tmp_path / "proj")
    workspace.execute("create_part_from_ops", {"name": "box", "ops": _BOX})
    return workspace


def test_the_default_export_is_named_relative_to_the_project(tmp_path):
    workspace = _workspace(tmp_path)
    written = workspace.execute("export_part", {"name": "box", "format": "step"})
    assert written["path"] == "exports/box.step"
    assert str(tmp_path) not in written["path"]
    assert (workspace.root / written["path"]).is_file()
    assert written["bytes"] == (workspace.root / written["path"]).stat().st_size


def test_a_named_export_is_named_relative_to_the_project(tmp_path):
    workspace = _workspace(tmp_path)
    relative = workspace.execute("export_part", {"name": "box", "format": "stl", "path": "out/parts/box.stl"})
    assert relative["path"] == "out/parts/box.stl"
    absolute = workspace.root.resolve() / "out" / "box.3mf"
    written = workspace.execute("export_part", {"name": "box", "format": "3mf", "path": str(absolute)})
    assert written["path"] == "out/box.3mf"
    assert absolute.is_file()


@pytest.mark.parametrize("raw", ["", "   "])
def test_an_empty_export_path_is_refused_and_writes_nothing(tmp_path, raw):
    workspace = _workspace(tmp_path)
    with pytest.raises(GeometryError, match="Export path is empty or not a usable path"):
        workspace.execute("export_part", {"name": "box", "format": "step", "path": raw})
    assert list(workspace.root.rglob("*.step")) == []
