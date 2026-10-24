"""A design session record stays inside the project and does not run itself.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, outside the project.
Replay and export must not create it, change it, or read it through a link.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image

from aicad.cli import main
from aicad.errors import GeometryError
from aicad.session_record import RecordRefused
from aicad.workspace import Workspace

_CANARY_NAME = "SANDBOX_CANARY.txt"
_CANARY_TEXT = "CANARY_HIT\n"
_BOX = [{"op": "add", "shape": "box", "size": [8, 3, 2], "center": [0, 0, 1]}]


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.delenv("AICAD_API_KEY", raising=False)
    return tmp_path


def _canary(directory: Path) -> Path:
    return directory / _CANARY_NAME


def _record(project: Path) -> dict:
    return json.loads((project / "records" / "session.json").read_text(encoding="utf-8"))


def _create(workspace: Workspace, name: str = "rail") -> None:
    workspace.execute(
        "create_part_from_ops",
        {"name": name, "ops": _BOX},
        source="ui",
    )


def test_recording_stays_off_until_it_is_turned_on(tmp_path, config_home):
    project = tmp_path / "project"
    workspace = Workspace(project)
    _create(workspace, "draft")
    assert not (project / "records" / "session.json").exists()
    assert workspace.state()["session_record"]["enabled"] is False

    started = workspace.execute("session_record", {"action": "start"}, source="ui")
    assert started["enabled"] is True
    assert started["steps"] == 0
    _create(workspace, "rail")
    status = workspace.execute("session_record", {"action": "status"}, source="ui")
    assert status["steps"] == 1
    assert status["summary"] == "Recording is on. 1 step is stored."
    saved = _record(project)
    assert saved["format"] == "session_record.v1"
    assert len(saved["steps"]) == 1
    step = saved["steps"][0]
    assert step["command"] == "create_part_from_ops"
    assert step["arguments"]["name"] == "rail"
    assert step["summary"]
    assert step["time"]
    assert step["state"]["parts"] == ["draft", "rail"]
    assert step["frame"] == "records/session/frames/0001.png"
    assert step["mesh"].startswith("records/session/meshes/")
    assert str(project) not in json.dumps(saved)
    assert (project / step["frame"]).is_file()

    workspace.execute("session_record", {"action": "stop"}, source="ui")
    _create(workspace, "second")
    assert len(_record(project)["steps"]) == 1


def test_a_shared_record_cannot_turn_recording_on(tmp_path, config_home):
    project = tmp_path / "project"
    workspace = Workspace(project)
    record = project / "records"
    record.mkdir()
    (record / "session.json").write_text(
        json.dumps({"format": "session_record.v1", "enabled": True, "steps": []}) + "\n",
        encoding="utf-8",
    )
    assert workspace.execute("session_record", {"action": "status"}, source="ui")["enabled"] is False
    _create(workspace)
    assert _record(project)["steps"] == []


def test_replay_steps_through_the_record_and_export_writes_a_gif(tmp_path, config_home):
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("session_record", {"action": "start"}, source="ui")
    _create(workspace, "rail")
    _create(workspace, "post")
    replay = workspace.execute("session_record", {"action": "replay"}, source="api")
    assert [step["command"] for step in replay["steps"]] == ["create_part_from_ops", "create_part_from_ops"]
    assert replay["steps"][1]["state"]["parts"] == ["rail", "post"]

    one = workspace.execute("session_record", {"action": "replay", "step": 1}, source="api")
    assert [step["index"] for step in one["steps"]] == [1]

    exported = workspace.execute(
        "session_record",
        {
            "action": "export",
            "steps": [2],
            "fps": 4,
            "camera": "front",
            "width": 32,
            "height": 32,
            "video": "gif",
            "path": "records/session/front.gif",
        },
        source="api",
    )
    assert exported["included"] == [2]
    assert exported["frames"] == 1
    assert exported["camera"] == "front"
    assert exported["camera_applied"] is True
    assert exported["gif"] == "records/session/front.gif"
    assert str(project) not in json.dumps(exported)
    with Image.open(project / exported["gif"]) as image:
        assert image.format == "GIF"
        assert image.n_frames == 1
        assert image.size == (32, 32)

    top = workspace.execute(
        "session_record",
        {
            "action": "export",
            "steps": [2],
            "camera": "top",
            "width": 32,
            "height": 32,
            "video": "gif",
            "path": "records/session/top.gif",
        },
        source="api",
    )
    assert (project / "records" / "session" / "front.gif").read_bytes() != (
        project / top["gif"]
    ).read_bytes()


def test_export_still_writes_a_gif_when_ffmpeg_is_missing(tmp_path, config_home, monkeypatch):
    monkeypatch.setattr("aicad.animation.shutil.which", lambda _name: None)
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("session_record", {"action": "start"}, source="ui")
    _create(workspace)
    exported = workspace.execute(
        "session_record",
        {"action": "export", "width": 32, "height": 32, "video": "mp4", "path": "records/session/clip.gif"},
        source="api",
    )
    assert (project / exported["gif"]).is_file()
    assert "mp4" not in exported
    assert "ffmpeg" in exported["video_message"]


def test_recording_drops_secrets_paths_and_outside_file_contents(tmp_path, config_home, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret_file = outside / "secret.txt"
    secret_file.write_text("UNIQUE_OUTSIDE_FILE_BODY\n", encoding="utf-8")
    monkeypatch.setenv("AICAD_API_KEY", "supersecretvalue99")
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("session_record", {"action": "start"}, source="ui")
    note = (
        f"token sk-abcdefghijklmnop and supersecretvalue99 "
        f"see {secret_file} and https://example.test/secret-path "
        f"and C:\\Users\\secret\\token.txt"
    )
    workspace.execute(
        "write_document",
        {"name": "notes.md", "markdown": note, "api_key": "another-secret-value"},
        source="agent",
    )
    text = (project / "records" / "session.json").read_text(encoding="utf-8")
    assert "supersecretvalue99" not in text
    assert "sk-abcdefghijklmnop" not in text
    assert "another-secret-value" not in text
    assert "UNIQUE_OUTSIDE_FILE_BODY" not in text
    assert "example.test" not in text
    assert str(secret_file) not in text
    assert "Users" not in text
    assert "<redacted>" in text
    assert "<link>" in text
    assert "<outside-project>" in text
    assert secret_file.read_text(encoding="utf-8") == "UNIQUE_OUTSIDE_FILE_BODY\n"


def test_recording_marks_a_drive_relative_path_outside(tmp_path, config_home):
    project = tmp_path / "project"
    workspace = Workspace(project)
    workspace.execute("session_record", {"action": "start"}, source="ui")
    inside = project / "notes.md"
    note = (
        r"open C:secret.txt and C:..\outside\token.txt and ....\....\hidden.txt "
        f"and keep {inside} plus exports/kept.step"
    )
    workspace.execute(
        "write_document",
        {"name": "notes.md", "markdown": note},
        source="agent",
    )
    text = (project / "records" / "session.json").read_text(encoding="utf-8")
    assert "secret.txt" not in text
    assert "token.txt" not in text
    assert "hidden.txt" not in text
    assert "<outside-project>" in text
    assert "notes.md" in text
    assert "exports/kept.step" in text
    assert str(inside) not in text


def test_the_command_line_records_a_step_without_an_outside_script(tmp_path, config_home):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    script = outside / "box.py"
    script.write_text("part = Box(2, 3, 4)\n# UNIQUE_OUTSIDE_SCRIPT_BODY\n", encoding="utf-8")
    assert main(["record", "start", "--workspace", str(project)]) == 0
    assert main(["run", str(script), "--name", "box", "--workspace", str(project)]) == 0
    saved = _record(project)
    text = json.dumps(saved)
    assert saved["steps"][0]["command"] == "create_part_from_script"
    assert "UNIQUE_OUTSIDE_SCRIPT_BODY" not in text
    assert str(script) not in text
    assert str(project) not in text
    assert main(["list", "--workspace", str(project)]) == 0
    assert len(_record(project)["steps"]) == 1
    assert main(["record", "stop", "--workspace", str(project)]) == 0
    ops = project / "ops.json"
    ops.write_text(json.dumps(_BOX), encoding="utf-8")
    assert main(["ops", str(ops), "--name", "rail", "--workspace", str(project)]) == 0
    assert len(_record(project)["steps"]) == 1


def test_replay_does_not_execute_a_record_or_follow_a_path_out(tmp_path, config_home, monkeypatch):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    workspace = Workspace(project)
    record = project / "records" / "session.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps(
            {
                "format": "session_record.v1",
                "enabled": True,
                "run": "os.system('touch SANDBOX_CANARY.txt')",
                "steps": [
                    {
                        "index": 1,
                        "time": "2026-10-24T00:00:00+00:00",
                        "command": "create_part_from_ops",
                        "arguments": {
                            "name": "pwned",
                            "ops": _BOX,
                            "script": "open('SANDBOX_CANARY.txt','w').write('CANARY_HIT')",
                            "page": "https://example.test/leave",
                        },
                        "summary": "do not run",
                        "source": "agent",
                        "state": {"parts": ["pwned"], "assemblies": [], "sketches": []},
                        "frame": "../outside/SANDBOX_CANARY.txt",
                        "mesh": "records/session/meshes/not-here.json",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    calls = []

    def refuse_subprocess(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("replay tried to start a process")

    monkeypatch.setattr("subprocess.run", refuse_subprocess)
    monkeypatch.setattr("os.system", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("os.system")))
    replay = workspace.execute("session_record", {"action": "replay"}, source="api")
    assert replay["steps"][0]["command"] == "create_part_from_ops"
    assert replay["steps"][0]["frame_omitted"] is True
    assert "example.test" not in json.dumps(replay)
    assert replay["steps"][0]["arguments"]["page"] == "<link>"
    assert workspace.document.list_parts() == []
    assert calls == []
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    with pytest.raises(RecordRefused):
        workspace.execute(
            "session_record",
            {"action": "export", "width": 32, "height": 32, "video": "gif"},
            source="api",
        )
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert not (outside / "timelapse.gif").exists()
    assert workspace.document.list_parts() == []


def test_a_linked_record_is_not_read(tmp_path, config_home):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    workspace = Workspace(project)
    folder = project / "records"
    folder.mkdir()
    link = folder / "session.json"
    try:
        link.symlink_to(canary)
    except OSError as exc:
        pytest.skip(f"this machine cannot create a symlink: {exc}")
    with pytest.raises(RecordRefused):
        workspace.execute("session_record", {"action": "replay"}, source="api")
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert _CANARY_TEXT not in "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in folder.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def test_a_record_with_another_name_is_not_read(tmp_path, config_home):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    workspace = Workspace(project)
    folder = project / "records"
    folder.mkdir()
    record = folder / "session.json"
    try:
        os.link(canary, record)
    except OSError as exc:
        pytest.skip(f"hard links are not available: {exc}")
    with pytest.raises(RecordRefused):
        workspace.execute("session_record", {"action": "replay"}, source="api")
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert "CANARY_HIT" not in json.dumps(workspace.execute("session_record", {"action": "status"}, source="api"))


def test_recording_does_not_follow_a_record_directory_link(tmp_path, config_home):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    workspace = Workspace(project)
    link = project / "records"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"this machine cannot create a directory symlink: {exc}")
    with pytest.raises(GeometryError):
        workspace.execute("session_record", {"action": "start"}, source="ui")
    _create(workspace)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert not (outside / "session.json").exists()
    assert not (outside / "session").exists()


def test_the_app_page_can_start_a_record(tmp_path, config_home):
    import threading
    from http.client import HTTPConnection

    from aicad.app import make_server

    project = tmp_path / "project"
    project.mkdir()
    server = make_server(project, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address

        def exchange(method, path, body=None, token=True):
            connection = HTTPConnection(host, port, timeout=30)
            headers = {"Origin": f"http://127.0.0.1:{port}"}
            if token:
                headers["X-Aicad-Token"] = server.page_token
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            status = response.status
            connection.close()
            return status, payload

        status, page = exchange("GET", "/", token=False)
        assert status == 200
        html = page.decode("utf-8")
        assert 'id="record-session"' in html
        assert 'id="record-timelapse"' in html
        started = json.dumps({"name": "session_record", "arguments": {"action": "start"}})
        status, payload = exchange("POST", "/api/command", started)
        assert status == 200
        assert json.loads(payload)["result"]["enabled"] is True
        created = json.dumps(
            {"name": "create_part_from_ops", "arguments": {"name": "rail", "ops": _BOX}}
        )
        status, payload = exchange("POST", "/api/command", created)
        assert status == 200
        assert json.loads(payload)["ok"] is True
        status, payload = exchange("GET", "/api/state")
        state = json.loads(payload)["result"]
        assert state["session_record"]["enabled"] is True
        assert state["session_record"]["steps"] == 1
    finally:
        server.shutdown()
        server.server_close()
