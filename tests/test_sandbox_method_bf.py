"""Opening a session record draws the stored mesh again.

The marker is CANARY_HIT. A replaced picture is drawn again from the mesh.
A step with no mesh does not return the stored picture.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from aicad.workspace import Workspace

_BOX = [{"op": "add", "shape": "box", "size": [8, 3, 2], "center": [0, 0, 1]}]


def _project(tmp_path: Path, monkeypatch) -> tuple[Path, Workspace]:
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.delenv("AICAD_API_KEY", raising=False)
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("session_record", {"action": "start"}, source="ui")
    workspace.execute(
        "create_part_from_ops",
        {"name": "rail", "ops": _BOX},
        source="ui",
    )
    return project, workspace


def _record(project: Path) -> dict:
    return json.loads((project / "records" / "session.json").read_text(encoding="utf-8"))


def _write_record(project: Path, payload: dict) -> None:
    (project / "records" / "session.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def _solid(path: Path, color: tuple[int, int, int]) -> bytes:
    Image.new("RGB", (128, 96), color).save(path, format="PNG")
    return path.read_bytes()


def _is_solid(data: bytes, color: tuple[int, int, int]) -> bool:
    with Image.open(BytesIO(data)) as image:
        colors = image.convert("RGB").getcolors(maxcolors=2)
    return colors == [(image_width_height(data), color)]


def image_width_height(data: bytes) -> int:
    with Image.open(BytesIO(data)) as image:
        return image.size[0] * image.size[1]


def test_replay_draws_a_replaced_frame_from_the_mesh(tmp_path, monkeypatch):
    project, workspace = _project(tmp_path, monkeypatch)
    saved = _record(project)
    frame = project / saved["steps"][0]["frame"]
    planted = _solid(frame, (255, 0, 0))
    replay = workspace.execute("session_record", {"action": "replay"}, source="api")
    assert replay["steps"][0]["frame"] == saved["steps"][0]["frame"]
    assert "frame_omitted" not in replay["steps"][0]
    restored = frame.read_bytes()
    assert restored != planted
    assert not _is_solid(restored, (255, 0, 0))
    assert b"CANARY_HIT" not in restored


def test_a_step_without_a_mesh_does_not_return_its_picture(tmp_path, monkeypatch):
    project, workspace = _project(tmp_path, monkeypatch)
    saved = _record(project)
    saved["steps"][0].pop("mesh")
    _write_record(project, saved)
    frame = project / saved["steps"][0]["frame"]
    planted = _solid(frame, (255, 0, 0))
    replay = workspace.execute("session_record", {"action": "replay"}, source="api")
    assert replay["steps"][0]["frame_omitted"] is True
    assert "frame" not in replay["steps"][0]
    assert frame.read_bytes() == planted
    exported = workspace.execute(
        "session_record",
        {"action": "export", "width": 32, "height": 32, "video": "gif"},
        source="api",
    )
    gif = (project / exported["gif"]).read_bytes()
    assert frame.read_bytes() == planted
    assert b"CANARY_HIT" not in gif
