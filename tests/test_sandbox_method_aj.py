"""A copy, an export, and a picture response do not follow a link.

The marker is CANARY_HIT. Bytes from outside the project are not written
and are not returned.
"""

from __future__ import annotations

import pytest

from aicad import kernel
from aicad.errors import GeometryError
from aicad.paths import remember_project
from aicad.render import render_views
from aicad.server import _picture_bytes
from aicad.studio import save_project_as
from aicad.workspace import Workspace
from PIL import Image


def test_a_picture_response_does_not_follow_a_link(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "secret.png"
    marker.write_bytes(b"CANARY_HIT")
    link = project / "view.png"
    link.symlink_to(marker)
    picture = project / "ok.png"
    Image.new("RGB", (1, 1), (1, 2, 3)).save(picture)
    remember_project(project)
    data = _picture_bytes(str(picture))
    assert data.startswith(b"\x89PNG")
    assert b"CANARY_HIT" not in data
    with pytest.raises(OSError):
        _picture_bytes(str(link))
    assert marker.read_bytes() == b"CANARY_HIT"


def test_a_render_does_not_follow_a_directory_link(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "CANARY_HIT").write_text("CANARY_HIT\n", encoding="utf-8")
    link = project / "renders"
    link.symlink_to(outside, target_is_directory=True)
    shape = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10]}])
    mesh = kernel.tessellate(shape)
    measurement = kernel.measure(shape)
    with pytest.raises(OSError):
        render_views(mesh, link, "part", measurement)
    assert (outside / "CANARY_HIT").read_text(encoding="utf-8") == "CANARY_HIT\n"
    assert not (outside / "part_front.png").exists()


def test_save_as_does_not_follow_a_directory_link(tmp_path):
    project = tmp_path / "project"
    workspace = Workspace(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "CANARY_HIT"
    marker.write_text("CANARY_HIT\n", encoding="utf-8")
    link = tmp_path / "copy"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(GeometryError, match="link"):
        save_project_as(workspace, str(link))
    assert marker.read_text(encoding="utf-8") == "CANARY_HIT\n"
    assert not (outside / "session.json").exists()
