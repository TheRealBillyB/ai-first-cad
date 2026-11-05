"""The app's geometry runs in its own process, so the page keeps answering."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from aicad.app import make_server
from aicad.app_worker import GeometryWorker, _error_payload, _rebuilt_error
from aicad.blueprint import BlueprintError
from aicad.errors import AICADError, EdgeOpError, GeometryError, PartNotFound, SandboxNotReady, ScriptError
from aicad.plugins import PluginConsentError, PluginError
from aicad.session_record import RecordRefused

_MANY_CUTS = [{"op": "add", "shape": "box", "size": [400, 400, 10]}] + [
    {"op": "cut", "shape": "cylinder", "radius": 3, "height": 30, "center": [(i % 15) * 25 - 180, (i // 15) * 25 - 120, 0]}
    for i in range(150)
]


@pytest.fixture
def worker(tmp_path):
    running = GeometryWorker(tmp_path / "project")
    yield running
    running.close()


_RECORD_GMSH_STARTS = """
import os, threading
import gmsh

real_initialize = gmsh.initialize

def initialize(*args, **kwargs):
    with open(os.environ["AICAD_TEST_GMSH_STARTS"], "a", encoding="utf-8") as log:
        log.write(f"{threading.get_ident()} {threading.main_thread().ident}\\n")
    return real_initialize(*args, **kwargs)

gmsh.initialize = initialize
# The mesh comes before the solve, so a computer without CalculiX still records it.
from aicad import fea
fea.fea_available = lambda: (True, "")
from aicad.app_worker import _main
raise SystemExit(_main())
"""


def _beam_setup(mesh_mm: float) -> dict:
    return {
        "material": "steel",
        "mesh_size_mm": mesh_mm,
        "target_safety_factor": 1.0,
        "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        "loads": [
            {"name": "tip", "type": "force", "vector": [0, 0, -100], "select": {"kind": "plane", "normal": [1, 0, 0]}}
        ],
    }


def test_every_mesh_in_the_worker_starts_gmsh_on_one_thread(tmp_path, monkeypatch):
    """Gmsh crashes on macOS when a new thread starts it. Each page request has its own thread."""
    pytest.importorskip("gmsh")
    starts = tmp_path / "gmsh-starts.txt"
    monkeypatch.setenv("AICAD_TEST_GMSH_STARTS", str(starts))
    monkeypatch.setattr(
        "aicad.app_worker._worker_command",
        lambda: [sys.executable, "-P", "-c", _RECORD_GMSH_STARTS],
    )
    running = GeometryWorker(tmp_path / "project")
    try:
        running.execute("create_part_from_ops", {"name": "beam", "ops": [{"op": "add", "shape": "box", "size": [20, 4, 4]}]}, "ui")
        process = running._process

        def solve(mesh_mm: float) -> None:
            try:
                running.execute("solve_mechanics", {"name": "beam", "setup": _beam_setup(mesh_mm)}, "ui")
            except AICADError:
                pass  # Without a solver the solve stops after the mesh, which is the part measured here.

        # A new thread for each request, the way the page's server calls the worker.
        for mesh_mm in (2.0, 2.5, 3.0):
            caller = threading.Thread(target=solve, args=(mesh_mm,))
            caller.start()
            caller.join(180)
            assert not caller.is_alive()
        assert running._process is process, "the worker was replaced during the solves"
    finally:
        running.close()
    rows = [line.split() for line in starts.read_text(encoding="utf-8").splitlines()]
    assert len(rows) >= 3
    assert len({row[0] for row in rows}) == 1
    assert rows[0][0] != rows[0][1]


# Like the app: Gmsh is imported at the first solve, after the worker already
# watches its stdin. A computer without CalculiX stops after the mesh.
_IMPORT_GMSH_AT_THE_FIRST_SOLVE = """
import sys
from aicad import fea

def available():
    import gmsh  # noqa: F401
    return True, ""

fea.fea_available = available
from aicad.app_worker import _main
raise SystemExit(_main())
"""


def test_the_first_solve_imports_gmsh_while_the_worker_watches_stdin(tmp_path, monkeypatch):
    """On Windows, a read waiting on stdin stopped Gmsh's DLL from loading, so the first solve hung."""
    pytest.importorskip("gmsh")
    monkeypatch.setattr(
        "aicad.app_worker._worker_command",
        lambda: [sys.executable, "-P", "-c", _IMPORT_GMSH_AT_THE_FIRST_SOLVE],
    )
    running = GeometryWorker(tmp_path / "project")
    try:
        running.execute("create_part_from_ops", {"name": "beam", "ops": [{"op": "add", "shape": "box", "size": [20, 4, 4]}]}, "ui")
        finished = threading.Event()

        def solve() -> None:
            try:
                running.execute("solve_mechanics", {"name": "beam", "setup": _beam_setup(3.0)}, "ui")
            except AICADError:
                pass  # Without a solver, or a sandbox on this computer, it stops after the mesh.
            finished.set()

        threading.Thread(target=solve, daemon=True).start()
        assert finished.wait(180), "the solve did not get past importing Gmsh"
    finally:
        running.close()


_WATCH_THEN_IMPORT = """
import sys, threading, time
from aicad.app_worker import _wait_until_the_app_closes_stdin

closed = threading.Event()

def watch():
    _wait_until_the_app_closes_stdin()
    closed.set()

threading.Thread(target=watch, daemon=True).start()
time.sleep(0.5)
import gmsh  # noqa: F401
print("imported", flush=True)
print("closed" if closed.wait(60) else "still watching", flush=True)
"""


def test_watching_stdin_lets_a_library_load_and_still_sees_the_app_close():
    pytest.importorskip("gmsh")
    child = subprocess.Popen(
        [sys.executable, "-P", "-c", _WATCH_THEN_IMPORT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        lines = []
        reader = threading.Thread(target=lambda: lines.append(child.stdout.readline().strip()), daemon=True)
        reader.start()
        reader.join(120)
        assert lines == ["imported"], "importing Gmsh waited on the stdin watcher"
        child.stdin.close()
        assert child.stdout.readline().strip() == "closed"
        assert child.wait(30) == 0
    finally:
        if child.poll() is None:
            child.kill()


def test_a_failure_comes_back_as_the_same_error():
    for raised in (GeometryError("no"), ScriptError("no"), PartNotFound("no")):
        rebuilt = _rebuilt_error(json.loads(json.dumps(_error_payload(raised))))
        assert type(rebuilt) is type(raised)
        assert str(rebuilt) == "no"


def test_a_failure_keeps_its_extra_fields():
    rebuilt = _rebuilt_error(json.loads(json.dumps(_error_payload(EdgeOpError("no", code="too_big")))))
    assert isinstance(rebuilt, EdgeOpError)
    assert rebuilt.code == "too_big"


@pytest.mark.parametrize("raised", [PluginError("no"), PluginConsentError("no"), BlueprintError("no")])
def test_an_expected_failure_from_outside_the_errors_module_stays_expected(raised):
    rebuilt = _rebuilt_error(json.loads(json.dumps(_error_payload(raised))))
    assert isinstance(rebuilt, AICADError)
    assert str(rebuilt) == "no"


def test_a_run_refused_for_a_missing_setup_keeps_its_own_error():
    """The app answers this one with the setup message, not a server error."""
    rebuilt = _rebuilt_error(json.loads(json.dumps(_error_payload(SandboxNotReady("run the setup")))))
    assert type(rebuilt) is SandboxNotReady
    assert str(rebuilt) == "run the setup"


def test_a_failure_becomes_the_nearest_error_the_app_knows():
    rebuilt = _rebuilt_error(json.loads(json.dumps(_error_payload(RecordRefused("no")))))
    assert type(rebuilt) is GeometryError


def test_an_unexpected_failure_is_still_not_expected():
    rebuilt = _rebuilt_error(json.loads(json.dumps(_error_payload(KeyError("oops")))))
    assert type(rebuilt) is RuntimeError


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


def test_a_mesh_is_not_kept_under_a_revision_it_does_not_show(worker):
    """A command that lands just after a mesh's reply moves the revision on."""
    box = [{"op": "add", "shape": "box", "size": [4, 4, 4]}]
    worker.execute("create_part_from_ops", {"name": "a", "ops": box}, "ui")
    worker.execute("create_part_from_ops", {"name": "b", "ops": box}, "ui")
    real = worker._request_lock

    class CommandLandsNext:
        armed = True

        def acquire(self, *args, **kwargs):
            return real.acquire(*args, **kwargs)

        def release(self):
            real.release()

        def __enter__(self):
            real.acquire()

        def __exit__(self, *_exc):
            real.release()
            if CommandLandsNext.armed:
                CommandLandsNext.armed = False
                worker.execute("delete_part", {"name": "b"}, "ui")

    worker._request_lock = CommandLandsNext()
    assert worker.mesh("b")["part"] == "b"
    worker._request_lock = real
    with pytest.raises(PartNotFound):
        worker.mesh("b")


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
    assert process.returncode == 0
    with pytest.raises(GeometryError, match="closed"):
        running.execute("list_parts")


def test_the_worker_stops_when_the_app_dies_without_closing(tmp_path):
    running = GeometryWorker(tmp_path / "project")
    process = running._process
    process.stdin.close()
    assert process.wait(20) == 0
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
