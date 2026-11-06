"""``aicad app`` prints a line when each command starts and when it ends."""

import json
import re
import threading
from http.client import HTTPConnection

import pytest

from aicad.app import make_server

_CREATE = {
    "name": "create_part_from_ops",
    "arguments": {"name": "block", "ops": [{"op": "add", "shape": "box", "size": [10, 10, 10]}]},
}
_MEASURE_MISSING = {"name": "measure_part", "arguments": {"name": "no_such_part"}}


def _start(tmp_path, monkeypatch, **options):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    running = make_server(workspace, "127.0.0.1", 0, **options)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    return running


@pytest.fixture
def logging_server(tmp_path, monkeypatch):
    running = _start(tmp_path, monkeypatch, log_commands=True)
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


@pytest.fixture
def quiet_server(tmp_path, monkeypatch):
    running = _start(tmp_path, monkeypatch)
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


def _command(server, command: dict) -> tuple[int, dict]:
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=60)
    headers = {
        "Origin": f"http://127.0.0.1:{port}",
        "X-Aicad-Token": server.page_token,
        "Content-Type": "application/json",
    }
    connection.request("POST", "/api/command", body=json.dumps(command), headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read())
    status = response.status
    connection.close()
    return status, payload


def test_app_logs_each_command_start_and_end_without_arguments(logging_server, capsys):
    capsys.readouterr()
    status, payload = _command(logging_server, _CREATE)
    assert status == 200, payload
    status, payload = _command(logging_server, _MEASURE_MISSING)
    assert status == 400, payload
    error_line = payload["error"].strip().splitlines()[0].strip()

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    stamp = r"\d\d:\d\d:\d\d"
    assert len(lines) == 4, lines
    assert re.fullmatch(stamp + r" person create_part_from_ops started", lines[0]), lines
    assert re.fullmatch(stamp + r" person create_part_from_ops ok after \d+\.\d s", lines[1]), lines
    assert re.fullmatch(stamp + r" person measure_part started", lines[2]), lines
    assert re.fullmatch(stamp + r" person measure_part failed after \d+\.\d s: .+", lines[3]), lines
    assert lines[3].endswith(": " + error_line[:200]), (lines[3], error_line)

    log = "\n".join(lines)
    assert "shape" not in log
    assert "box" not in log
    # The error may name parts in the document; the create lines must not echo arguments.
    assert "block" not in lines[0] + lines[1]
    assert all(line.isascii() for line in lines)


def test_make_server_without_log_commands_prints_nothing(quiet_server, capsys):
    capsys.readouterr()
    status, payload = _command(quiet_server, _CREATE)
    assert status == 200, payload
    assert capsys.readouterr().out == ""
