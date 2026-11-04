"""The app's geometry runs in its own process, so the page keeps answering."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

import pytest

from aicad.app import make_server
from aicad.app_worker import GeometryWorker, _error_payload, _rebuilt_error
from aicad.errors import EdgeOpError, GeometryError, PartNotFound, ScriptError

_MANY_CUTS = [{"op": "add", "shape": "box", "size": [400, 400, 10]}] + [
    {"op": "cut", "shape": "cylinder", "radius": 3, "height": 30, "center": [(i % 15) * 25 - 180, (i // 15) * 25 - 120, 0]}
    for i in range(150)
]


@pytest.fixture
def worker(tmp_path):
    running = GeometryWorker(tmp_path / "project")
    yield running
    running.close()


def test_a_failure_comes_back_as_the_same_error():
    for raised in (GeometryError("no"), ScriptError("no"), PartNotFound("no")):
        rebuilt = _rebuilt_error(json.loads(json.dumps(_error_payload(raised))))
        assert type(rebuilt) is type(raised)
        assert str(rebuilt) == "no"


def test_a_failure_keeps_its_extra_fields():
    rebuilt = _rebuilt_error(json.loads(json.dumps(_error_payload(EdgeOpError("no", code="too_big")))))
    assert isinstance(rebuilt, EdgeOpError)
    assert rebuilt.code == "too_big"


def test_an_unknown_failure_is_not_presented_as_an_expected_one():
    rebuilt = _rebuilt_error({"type": "KeyError", "message": "oops"})
    assert type(rebuilt) is RuntimeError
    assert _rebuilt_error({"type": "__builtins__", "message": "oops"}).__class__ is RuntimeError


def test_commands_run_in_the_other_process_and_errors_keep_their_type(worker):
    assert worker._process.pid != os.getpid()
    assert worker.execute("list_parts")["parts"] == []
    assert any(item["name"] == "list_parts" for item in worker.commands())
    with pytest.raises(GeometryError, match="Unknown command"):
        worker.execute("no_such_command")
    assert worker.root.name == "project"


def test_the_worker_only_answers_the_methods_the_app_uses(worker):
    connection = worker._connection
    connection.send_bytes(json.dumps({"method": "_restore", "args": []}).encode("utf-8"))
    answer = json.loads(connection.recv_bytes().decode("utf-8"))
    assert answer["ok"] is False


def test_events_reach_a_listener_and_stop_after_unsubscribing(worker):
    seen: list[dict] = []
    unsubscribe = worker.listen(seen.append)
    worker.execute("write_document", {"name": "note.md", "markdown": "# Note\n"}, "ui")
    assert [event["command"] for event in seen] == ["write_document"]
    assert seen[0]["source"] == "ui"
    unsubscribe()
    worker.execute("write_document", {"name": "note.md", "markdown": "# Again\n"}, "ui")
    assert len(seen) == 1


def test_a_read_while_a_command_runs_returns_at_once_and_says_busy(worker):
    fresh = worker.state()
    assert "busy" not in fresh
    worker._request_lock.acquire()
    worker._running = ("check_strength", time.monotonic())
    try:
        started = time.monotonic()
        state = worker.state()
        assert time.monotonic() - started < 0.2
    finally:
        worker._running = None
        worker._request_lock.release()
    assert state["busy"]["command"] == "check_strength"
    assert state["revision"] == fresh["revision"]
    assert "busy" not in worker.state()


def test_a_kept_mesh_does_not_wait_for_a_running_command(worker):
    worker._meshes[("mesh", "box")] = (worker.geometry_revision, {"part": "box"})
    worker._request_lock.acquire()
    try:
        assert worker.mesh("box") == {"part": "box"}
    finally:
        worker._request_lock.release()


def test_a_mesh_from_another_revision_is_not_used(worker):
    worker._meshes[("mesh", "box")] = (worker.geometry_revision - 1, {"part": "old"})
    with pytest.raises(Exception, match="box"):
        worker.mesh("box")


def test_a_worker_that_dies_is_replaced_and_the_project_is_still_there(worker):
    worker.execute("write_document", {"name": "kept.md", "markdown": "# Kept\n"}, "ui")
    before = worker.geometry_revision
    seen: list[dict] = []
    worker.listen(seen.append)
    old = worker._process
    old.kill()
    old.wait()
    with pytest.raises(GeometryError, match="restarted from the saved project"):
        worker.execute("list_documents")
    assert worker._process is not old
    assert worker.geometry_revision > before
    assert seen[-1]["command"] == "restart"
    assert "kept.md" in worker.execute("list_documents")["documents"]


def test_the_worker_stops_with_the_app(tmp_path):
    running = GeometryWorker(tmp_path / "project")
    process = running._process
    running.close()
    assert process.poll() is not None
    with pytest.raises(GeometryError, match="closed"):
        running.execute("list_parts")


def test_the_worker_stops_when_the_app_dies_without_closing(tmp_path):
    running = GeometryWorker(tmp_path / "project")
    process = running._process
    process.stdin.close()
    process.wait(20)
    running._process = None
    running.close()


def test_a_project_that_cannot_open_says_why(tmp_path):
    (tmp_path / "file").write_text("not a folder", encoding="utf-8")
    with pytest.raises(Exception, match="."):
        GeometryWorker(tmp_path / "file")


def _state_read(server) -> tuple[float, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}/api/state",
        headers={"X-Aicad-Token": server.page_token},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.load(response)
    return time.monotonic() - started, body


def test_the_page_keeps_answering_through_real_geometry(tmp_path, monkeypatch):
    """The freeze: in one process, a read waited for the whole boolean."""
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "servers"))
    server = make_server(tmp_path / "project", "127.0.0.1", 0, worker=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    worker = server.workspace
    done = threading.Event()
    finished: dict = {}

    def heavy() -> None:
        try:
            finished["result"] = worker.execute("create_part_from_ops", {"name": "plate", "ops": _MANY_CUTS}, "ui")
        finally:
            done.set()

    runner = threading.Thread(target=heavy, daemon=True)
    try:
        runner.start()
        slowest = 0.0
        said_busy = 0
        while not done.is_set():
            seconds, body = _state_read(server)
            assert body["ok"] is True
            slowest = max(slowest, seconds)
            said_busy += "busy" in body["result"]
            time.sleep(0.02)
        assert said_busy >= 3
        assert slowest < 2.0
        assert "part" in finished["result"]
        assert "busy" not in _state_read(server)[1]["result"]
    finally:
        runner.join(120)
        server.shutdown()
        server.server_close()
    assert worker._process is None
