"""The app's geometry runs in a process of its own, so the page keeps answering.

OpenCascade holds Python's lock through a long boolean, mesh, or solve. In one
process, every other request then waits, including the page itself. Threads do
not help. Here the app process only serves the page and forwards work. The
worker process owns the one real ``Workspace``, so permissions and the undo
history work the way they do in one process.

One request runs at a time, which is what the workspace lock already meant.
While one runs, ``state`` returns the last state with a ``busy`` entry, and a
mesh already read for this revision comes back without waiting. Any other
request waits on its own connection and holds up nothing else.

If the worker dies, that request fails with a clear message and a new worker
opens the saved project. Undo history and the selection were in the old one.

``aicad app`` does not wait for the first worker. The page answers at once and
``state`` says the project is opening. Any other request is refused with a
message that says so, and nothing is changed.

The pipe carries JSON, and it opens only with a key sent over the worker's
stdin. The worker stops when that stdin closes, so it cannot outlive the app.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path

from aicad import errors
from aicad.errors import AICADError, GeometryError

_START_SECONDS = 180.0
_STOP_SECONDS = 5.0
# A command shorter than this does not change the page.
_BUSY_NOTICE_SECONDS = 1.0
_KEPT_MESHES = 8
_FAMILY = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


def _worker_command() -> list[str]:
    return [sys.executable, "-P", "-m", "aicad.app_worker"]


def _error_payload(exc: BaseException) -> dict:
    fields = {}
    for key, value in vars(exc).items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        fields[key] = value
    # Every class name, nearest first. Some AICADError classes live outside aicad.errors.
    kinds = [kind.__name__ for kind in type(exc).__mro__]
    return {"type": type(exc).__name__, "kinds": kinds, "message": str(exc), "fields": fields}


def _rebuilt_error(payload: dict) -> Exception:
    """The nearest error the app knows, so it answers the failure the same way.

    An expected failure stays an expected failure, even when its own class is
    defined outside aicad.errors. Anything else is a RuntimeError.
    """
    message = str(payload.get("message") or "The geometry process reported a failure.")
    for name in payload.get("kinds") or [payload.get("type")]:
        kind = getattr(errors, str(name), None)
        if isinstance(kind, type) and issubclass(kind, AICADError):
            # Some errors take more constructor arguments. Their values come back as fields.
            exc = kind.__new__(kind)
            Exception.__init__(exc, message)
            exc.__dict__.update(payload.get("fields") or {})
            return exc
    return RuntimeError(message)


class ProjectStillOpening(GeometryError):
    """The worker has not finished opening the project."""

    def __init__(self, seconds: float) -> None:
        super().__init__(
            f"aicad is still opening the project ({seconds:.0f} s so far). Nothing was changed. "
            "Send the same request again in a few seconds."
        )


class GeometryWorker:
    """What the app needs from a ``Workspace``, answered by the worker process.

    With ``wait`` false, the first worker opens the project in the background.
    """

    def __init__(self, path: str | Path, *, wait: bool = True) -> None:
        self._request_lock = threading.Lock()
        self._listeners_lock = threading.Lock()
        self._mesh_lock = threading.Lock()
        self._listeners: list[Callable[[dict], None]] = []
        self._meshes: dict[tuple, tuple[int, dict]] = {}
        self._running: tuple[str, float] | None = None
        self._process: subprocess.Popen | None = None
        self._connection: Connection | None = None
        self._closed = False
        self.root = Path(path)
        self.geometry_revision = 0
        self._event_id = 0
        self._state: dict = {}
        self._commands: list[dict] = []
        self._open = threading.Event()
        self._open_error: str | None = None
        self._opening_since = time.monotonic()
        if wait:
            self._start()
            self._open.set()
        else:
            threading.Thread(target=self._open_in_background, name="aicad-worker-open", daemon=True).start()

    # What the app calls.

    def wait_open(self, timeout: float | None = None) -> str | None:
        """Wait for the first worker. None means the project is open, and text is why it did not open."""
        if not self._open.wait(timeout):
            return f"It is still opening after {timeout:.0f} seconds."
        return self._open_error

    def listen(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        with self._listeners_lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            with self._listeners_lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    def execute(self, name: str, arguments: dict | None = None, source: str = "api") -> dict:
        return self._call(name or "command", "execute", name, arguments, source)

    def state(self) -> dict:
        if not self._open.is_set() or self._open_error is not None:
            return {
                "opening": {
                    "seconds": round(time.monotonic() - self._opening_since),
                    "error": self._open_error,
                },
                "project": {"path": str(self.root), "name": self.root.name},
            }
        if self._request_lock.acquire(blocking=False):
            try:
                self._state = self._exchange("state", "state", ())
            finally:
                self._request_lock.release()
            return dict(self._state)
        state = dict(self._state)
        running = self._running
        if running is not None:
            state["busy"] = {"command": running[0], "seconds": round(time.monotonic() - running[1], 1)}
        return state

    def commands(self) -> list[dict]:
        self._require_open()
        return self._commands

    def mesh(self, name: str) -> dict:
        return self._mesh("mesh", name)

    def scene_mesh(self) -> dict:
        return self._mesh("scene_mesh")

    def assembly_mesh(self, name: str) -> dict:
        return self._mesh("assembly_mesh", name)

    def script_status(self) -> dict:
        return self._call("script_status", "script_status")

    def allow_script_network_once(self) -> dict:
        return self._call("allow_script_network_once", "allow_script_network_once")

    def close(self) -> None:
        self._closed = True
        self._stop_process()

    # How a request reaches the worker.

    def _require_open(self) -> None:
        if not self._open.is_set():
            raise ProjectStillOpening(time.monotonic() - self._opening_since)
        if self._open_error is not None:
            raise GeometryError(f"The project did not open, so nothing was changed. {self._open_error}")

    def _mesh(self, method: str, *args: str) -> dict:
        self._require_open()
        key = (method, *args)
        with self._mesh_lock:
            kept = self._meshes.get(key)
            if kept is not None and kept[0] == self.geometry_revision:
                return kept[1]
        with self._request_lock:
            result = self._exchange("the view", method, args)
            # From this reply. Once the lock is released, a command can move the revision on.
            revision = self.geometry_revision
        with self._mesh_lock:
            if revision == self.geometry_revision:
                self._meshes = {k: v for k, v in self._meshes.items() if v[0] == revision}
                while len(self._meshes) >= _KEPT_MESHES:
                    self._meshes.pop(next(iter(self._meshes)))
                self._meshes[key] = (revision, result)
        return result

    def _call(self, label: str, method: str, *args) -> dict:
        self._require_open()
        with self._request_lock:
            return self._exchange(label, method, args)

    def _exchange(self, label: str, method: str, args: tuple) -> dict:
        """One request and its answer. The caller holds the request lock."""
        if self._connection is None:
            self._restart()
        self._running = (label, time.monotonic())
        notice = threading.Timer(_BUSY_NOTICE_SECONDS, self._announce_busy, args=(label,))
        notice.daemon = True
        notice.start()
        try:
            try:
                self._connection.send_bytes(json.dumps({"method": method, "args": args}).encode("utf-8"))
                while True:
                    message = json.loads(self._connection.recv_bytes().decode("utf-8"))
                    if "event" not in message:
                        break
                    self._dispatch(message["event"])
            except (EOFError, OSError) as exc:
                raise self._worker_lost(label) from exc
        finally:
            notice.cancel()
            self._running = None
        self._remember(message)
        if not message["ok"]:
            raise _rebuilt_error(message["error"])
        return message["result"]

    def _remember(self, message: dict) -> None:
        self.root = Path(message["root"])
        self.geometry_revision = message["revision"]
        self._event_id = message["event_id"]

    def _dispatch(self, event: dict) -> None:
        with self._listeners_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener(event)

    def _announce_busy(self, label: str) -> None:
        # The page refreshes on any event, and its refresh then shows the busy note.
        self._dispatch({"event": self._event_id, "revision": self.geometry_revision, "command": label, "source": "app"})

    # Starting, stopping, and replacing the process.

    def _open_in_background(self) -> None:
        try:
            self._start()
        except Exception as exc:  # The page and every request show this text.
            self._open_error = str(exc) or type(exc).__name__
        if self._closed:
            # The app closed while this one was starting.
            self._stop_process()
        self._open.set()
        if self._open_error is None and not self._closed:
            # The page refreshes on any event, so it fills in now.
            self._dispatch({"event": self._event_id, "revision": self.geometry_revision, "command": "open", "source": "app"})

    def _start(self) -> None:
        key = secrets.token_bytes(32)
        listener = Listener(family=_FAMILY, authkey=key)
        try:
            self._process = subprocess.Popen(
                _worker_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
            )
            start = {"address": listener.address, "key": key.hex(), "workspace": str(self.root)}
            self._process.stdin.write(json.dumps(start).encode("utf-8") + b"\n")
            self._process.stdin.flush()
            connection = self._accept(listener)
            ready = json.loads(connection.recv_bytes().decode("utf-8"))
        except (EOFError, OSError) as exc:
            self._stop_process()
            raise GeometryError(f"The geometry process did not start. {exc}") from exc
        finally:
            listener.close()
        if not ready["ok"]:
            connection.close()
            self._stop_process()
            raise _rebuilt_error(ready["error"])
        self._connection = connection
        self._remember(ready)
        self._state = ready["state"]
        self._commands = ready["commands"]

    def _accept(self, listener: Listener) -> Connection:
        found: dict = {}

        def wait() -> None:
            try:
                found["connection"] = listener.accept()
            except Exception as exc:  # A wrong key or a closed pipe. The wait below reports it.
                found["error"] = exc

        waiting = threading.Thread(target=wait, name="aicad-worker-accept", daemon=True)
        waiting.start()
        deadline = time.monotonic() + _START_SECONDS
        while waiting.is_alive():
            waiting.join(0.1)
            if waiting.is_alive() and self._process.poll() is not None:
                raise OSError(f"It exited with code {self._process.returncode}.")
            if time.monotonic() > deadline:
                raise OSError(f"It did not answer in {_START_SECONDS:.0f} seconds.")
        if "error" in found:
            raise OSError(str(found["error"]))
        return found["connection"]

    def _stop_process(self) -> None:
        connection, self._connection = self._connection, None
        process, self._process = self._process, None
        if process is not None:
            try:
                # Closing stdin is the signal to stop, so it goes before the connection.
                process.stdin.close()
            except OSError:
                pass
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is None:
            return
        try:
            process.wait(_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _worker_lost(self, label: str) -> GeometryError:
        try:
            self._restart()
        except (AICADError, EOFError, OSError) as exc:
            self._stop_process()
            return GeometryError(f"The geometry process stopped while running {label}, and it did not restart. {exc}")
        return GeometryError(
            f"The geometry process stopped while running {label}, so that step did not finish. "
            "It has restarted from the saved project. Undo history and the selection were cleared."
        )

    def _restart(self) -> None:
        if self._closed:
            raise GeometryError("This workspace has closed.")
        self._stop_process()
        revision, event_id = self.geometry_revision, self._event_id
        self._start()
        # The page compares revisions, so the count keeps moving forward.
        self._running = None
        self._connection.send_bytes(json.dumps({"method": "resume", "args": [revision + 1, event_id + 1]}).encode("utf-8"))
        self._remember(json.loads(self._connection.recv_bytes().decode("utf-8")))
        self._dispatch({"event": self._event_id, "revision": self.geometry_revision, "command": "restart", "source": "app"})


# The worker process.


def _encoded(workspace, body: dict) -> bytes:
    body.update(root=str(workspace.root), revision=workspace.geometry_revision, event_id=workspace.event_id)
    return json.dumps(body).encode("utf-8")


def _send(connection: Connection, send_lock: threading.Lock, data: bytes) -> None:
    with send_lock:
        connection.send_bytes(data)


def _serve(start: dict) -> None:
    connection = Client(start["address"], family=_FAMILY, authkey=bytes.fromhex(start["key"]))
    send_lock = threading.Lock()
    try:
        from aicad.workspace import Workspace

        workspace = Workspace(start["workspace"])
        state = workspace.state()
        commands = workspace.commands()
    except Exception as exc:
        connection.send_bytes(json.dumps({"ok": False, "error": _error_payload(exc)}).encode("utf-8"))
        return

    def send_event(event: dict) -> None:
        _send(connection, send_lock, json.dumps({"event": event}).encode("utf-8"))

    workspace.listen(send_event)
    _send(connection, send_lock, _encoded(workspace, {"ok": True, "state": state, "commands": commands}))

    def resume(revision: int, event_id: int) -> dict:
        workspace.geometry_revision = revision
        workspace.event_id = event_id
        return {}

    # A name the app did not list cannot be called, including anything private.
    methods = {
        "execute": workspace.execute,
        "state": workspace.state,
        "script_status": workspace.script_status,
        "mesh": workspace.mesh,
        "scene_mesh": workspace.scene_mesh,
        "assembly_mesh": workspace.assembly_mesh,
        "allow_script_network_once": workspace.allow_script_network_once,
        "resume": resume,
    }
    while True:
        try:
            request = json.loads(connection.recv_bytes().decode("utf-8"))
        except (EOFError, OSError):
            return
        try:
            result = methods[request["method"]](*request["args"])
            data = _encoded(workspace, {"ok": True, "result": result})
        except Exception as exc:  # The app decides what a failure means.
            data = _encoded(workspace, {"ok": False, "error": _error_payload(exc)})
        try:
            _send(connection, send_lock, data)
        except OSError:
            return


def _main() -> int:
    start = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
    # Ctrl-C reaches both processes. The app decides when this one stops.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def stop_with_the_app() -> None:
        sys.stdin.buffer.read()
        os._exit(0)

    threading.Thread(target=stop_with_the_app, name="aicad-worker-parent", daemon=True).start()
    # A normal interpreter exit aborts while that thread holds stdin's lock, so this exit skips it.
    code = 1
    try:
        _serve(start)
        code = 0
    finally:
        sys.stderr.flush()
        os._exit(code)


if __name__ == "__main__":
    raise SystemExit(_main())
