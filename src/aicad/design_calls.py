"""Document calls for a design script.

The script asks for a call. A separate process runs it and records the result.
The script cannot supply that result.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import time
from pathlib import Path

_REQUEST_LIMIT = 2_000_000
_RESPONSE_LIMIT = 32_000_000
_BUSY_TRIES = 200


class _View(dict):
    """A JSON object the script can read with attributes or keys."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _restore(value):
    if isinstance(value, dict):
        return _View({str(key): _restore(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_restore(item) for item in value]
    return value


def _busy(exc: OSError) -> bool:
    """A sharing lock while another writer still has the file.

    A link, a file past the size limit, and a file that is not JSON are not busy.
    """
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "winerror", None) in (32, 33):
        return True
    return exc.errno in (errno.EACCES, errno.EPERM, errno.EAGAIN, errno.EWOULDBLOCK)


def _read_json(path: Path, limit: int) -> dict | None:
    from aicad.file_access import read_from_root
    from aicad.paths import _TooLarge, _before_nofollow

    parent = path.parent
    relative = Path(path.name)
    _before_nofollow(parent, relative)
    try:
        raw = read_from_root(parent, relative, limit)
    except _TooLarge as exc:
        raise OSError(errno.EFBIG, "too large") from exc
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EINVAL} or exc.strerror in {
            "is a link",
            "has another name",
            "not an ordinary file",
        }:
            raise OSError(errno.ELOOP, "link") from exc
        raise
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OSError(errno.EINVAL, "not json") from exc
    if not isinstance(payload, dict):
        raise OSError(errno.EINVAL, "not json")
    return payload


def _write_json(path: Path, payload: dict) -> None:
    text = json.dumps(payload) + "\n"
    if len(text.encode("utf-8")) > _RESPONSE_LIMIT:
        payload = {"ok": False, "error_type": "RuntimeError", "error": "The result was too large."}
        text = json.dumps(payload) + "\n"
    from aicad.paths import publish_bytes

    publish_bytes(path, text.encode("utf-8"), root=path.parent)


def _raise_remote(error_type: str, message: str) -> None:
    import aicad.errors as errors

    cls = getattr(errors, error_type, None)
    if isinstance(cls, type) and issubclass(cls, Exception):
        raise cls(message) from None
    builtin = {"TypeError": TypeError, "ValueError": ValueError, "AttributeError": AttributeError, "RuntimeError": RuntimeError}.get(error_type)
    if builtin is not None:
        raise builtin(message) from None
    raise RuntimeError(f"{error_type}: {message}") from None


def _dispatch_name(name: str, args: tuple, kwargs: dict):
    from aicad.parity import SCRIPT_METHODS
    from aicad.sandbox_run import _unavailable

    if name not in SCRIPT_METHODS:
        raise _unavailable(name)
    return name, args, kwargs


def _recorded(result):
    """Text for the report. A value that cannot be stored still names the call."""
    from aicad.sandbox_run import _jsonable

    try:
        return _jsonable(result)
    except Exception:
        pass
    try:
        return str(result)
    except Exception:
        return type(result).__name__


def design_proxy(directory: Path | None = None, live: dict | None = None, *, call_directory=None):
    """The ``document`` name. Calls are sent to ``directory`` and run elsewhere.

    ``live`` is only for a test in this process. The script process does not have it.
    ``call_directory`` creates a fresh directory for one call. That directory is
    removed when the call returns. The subprocess path keeps one directory.
    """
    import threading

    from aicad.parity import SCRIPT_METHODS
    from aicad.sandbox_run import ScriptDocument

    if directory is None and call_directory is None:
        raise RuntimeError("The call was refused.")
    state = {"n": 0}
    lock = threading.Lock()

    def submit(op: str, name: str | None, args, kwargs) -> tuple[dict, int]:
        with lock:
            number = state["n"]
            state["n"] += 1
        owned = None
        if call_directory is not None:
            folder = Path(call_directory())
            owned = folder
        else:
            folder = directory
        try:
            request = {"op": op, "method": name, "args": list(args), "kwargs": dict(kwargs)}
            encoded = json.dumps(request)
            if len(encoded.encode("utf-8")) > _REQUEST_LIMIT:
                raise RuntimeError("The call was too large.")
            path = folder / f"{number}.req.json"
            _write_json(path, request)
            response_path = folder / f"{number}.res.json"
            busy = 0
            while True:
                if (folder / "STOP").exists() and not response_path.exists():
                    raise RuntimeError("The call was refused.")
                try:
                    payload = _read_json(response_path, _RESPONSE_LIMIT)
                except OSError as exc:
                    if _busy(exc) and busy < _BUSY_TRIES:
                        busy += 1
                        time.sleep(0.01)
                        continue
                    raise RuntimeError("The call was refused.") from exc
                busy = 0
                if payload is not None:
                    return payload, number
                time.sleep(0.01)
        finally:
            if owned is not None:
                shutil.rmtree(owned, ignore_errors=True)

    def dispatch(name: str, args, kwargs):
        _dispatch_name(name, tuple(args), dict(kwargs))
        payload, number = submit("call", name, args, kwargs)
        if not payload.get("ok"):
            _raise_remote(str(payload.get("error_type") or "RuntimeError"), str(payload.get("error") or ""))
        if live is not None and number in live:
            return live.pop(number)
        return _restore(payload.get("result"))

    def finish() -> None:
        payload, _number = submit("finish", None, (), {})
        if not payload.get("ok"):
            _raise_remote(str(payload.get("error_type") or "RuntimeError"), str(payload.get("error") or ""))

    wrappers = {}
    for name in SCRIPT_METHODS:
        namespace = {"dispatch": dispatch, "name": name, "AttributeError": AttributeError, "__builtins__": {}}
        exec("def call(*args, **kwargs):\n    return dispatch(name, args, kwargs)\n", namespace)  # noqa: S102
        wrappers[name] = namespace["call"]
    return ScriptDocument(wrappers), finish


def _effects(document, before: dict, calls: list[dict]) -> dict:
    from aicad.sandbox_run import _CHECKS, _RENDER_LIMIT

    after = {row["name"]: row for row in document.session_payload().get("parts", [])}
    changed = [name for name, row in after.items() if before.get(name) != row]
    deleted = [name for name in before if name not in after]
    notes: list[str] = []
    views = []
    rendered = 0
    for name in changed:
        if rendered >= _RENDER_LIMIT:
            notes.append(f"Only the first {_RENDER_LIMIT} changed parts were rendered.")
            break
        try:
            for view in document.render(name):
                views.append(view.to_dict())
            rendered += 1
        except Exception as exc:
            notes.append(f"Could not render {name}: {exc}")
    changed_parts = []
    for info in document.list_parts():
        if info.name in changed:
            changed_parts.append(info.to_dict())
    checks = [call for call in calls if call.get("method") in _CHECKS]
    return {
        "checks": checks,
        "changed_parts": changed_parts,
        "deleted_parts": deleted,
        "views": views,
        "notes": notes,
    }


def serve_calls(directory: Path, document, calls: list[dict], before: dict, live: dict | None = None) -> dict:
    """Run calls from ``directory`` until ``STOP`` appears. Return the report."""
    directory.mkdir(parents=True, exist_ok=True)
    number = 0
    busy = 0
    while True:
        stop = directory / "STOP"
        if stop.is_symlink() or stop.exists():
            break
        path = directory / f"{number}.req.json"
        if not path.exists():
            time.sleep(0.01)
            continue
        try:
            request = _read_json(path, _REQUEST_LIMIT)
        except OSError as exc:
            if _busy(exc) and busy < _BUSY_TRIES:
                busy += 1
                time.sleep(0.01)
                continue
            busy = 0
            _write_json(
                directory / f"{number}.res.json",
                {"ok": False, "error_type": "RuntimeError", "error": str(exc)},
            )
            number += 1
            continue
        busy = 0
        if request is None:
            time.sleep(0.01)
            continue
        _apply_request(directory, number, request, document, calls, before, live)
        number += 1
    return _effects(document, before, calls)


def _apply_request(directory: Path, number: int, request: dict, document, calls: list[dict], _before: dict, live: dict | None) -> None:
    """Run one request and write its response. The report reads ``_before`` later."""
    from aicad.parity import SCRIPT_METHODS
    if request.get("op") == "finish":
        _write_json(directory / f"{number}.res.json", {"ok": True})
        return
    name = request.get("method")
    args = request.get("args")
    kwargs = request.get("kwargs")
    if not isinstance(name, str) or name not in SCRIPT_METHODS or not isinstance(args, list) or not isinstance(kwargs, dict):
        _write_json(
            directory / f"{number}.res.json",
            {"ok": False, "error_type": "AttributeError", "error": "The call was refused."},
        )
        return
    if any(not isinstance(key, str) for key in kwargs):
        _write_json(
            directory / f"{number}.res.json",
            {"ok": False, "error_type": "TypeError", "error": "The call was refused."},
        )
        return
    try:
        result = getattr(document, name)(*args, **kwargs)
    except Exception as exc:
        calls.append({"method": name, "error": f"{type(exc).__name__}: {exc}"})
        _write_json(
            directory / f"{number}.res.json",
            {"ok": False, "error_type": type(exc).__name__, "error": str(exc)},
        )
        return
    if live is not None:
        live[number] = result
    calls.append({"method": name, "result": _recorded(result)})
    _write_json(directory / f"{number}.res.json", {"ok": True, "result": calls[-1]["result"]})


def _serve_one(directory: Path, document, calls: list[dict], before: dict, live: dict | None) -> None:
    """Run the one request in ``directory``. Return when it is gone or answered."""
    busy = 0
    while directory.exists():
        paths = sorted(item for item in directory.glob("*.req.json") if item.is_file() and not item.is_symlink())
        if not paths:
            time.sleep(0.01)
            continue
        path = paths[0]
        number_text = path.name.split(".", 1)[0]
        if not number_text.isdigit():
            time.sleep(0.01)
            continue
        number = int(number_text)
        try:
            request = _read_json(path, _REQUEST_LIMIT)
        except OSError as exc:
            if _busy(exc) and busy < _BUSY_TRIES:
                busy += 1
                time.sleep(0.01)
                continue
            busy = 0
            _write_json(
                directory / f"{number}.res.json",
                {"ok": False, "error_type": "RuntimeError", "error": str(exc)},
            )
            return
        busy = 0
        if request is None:
            time.sleep(0.01)
            continue
        _apply_request(directory, number, request, document, calls, before, live)
        return


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while len(view):
        count = os.write(descriptor, view)
        if count <= 0:
            raise OSError(errno.EIO, "could not write the design record")
        view = view[count:]
    os.fsync(descriptor)


def _adopt_record(record_fd: int) -> int:
    """Use the file the parent already opened.

    On Windows ``record_fd`` is the inherited handle, not a C runtime descriptor.
    """

    if sys.platform == "win32":
        import msvcrt

        return msvcrt.open_osfhandle(int(record_fd), os.O_WRONLY | getattr(os, "O_BINARY", 0))
    if record_fd < 0:
        raise OSError(errno.EBADF, "design record is not open")
    return int(record_fd)


def model_main(job: Path, record_path: Path, record_fd: int | None = None) -> int:
    """Run document calls for one design script. This process is not the script."""
    from aicad.document import Document
    from aicad.file_access import open_inplace

    workspace = Path(os.environ["AICAD_WORKSPACE"]).resolve()
    mode = os.environ.get("AICAD_WIN_SANDBOX_MODE", "")
    if mode in ("lpac", "appcontainer"):
        from aicad.sandbox_windows import adopt_probed_mode

        adopt_probed_mode(mode)
    document = Document(workspace)
    before = {row["name"]: row for row in document.session_payload().get("parts", [])}
    calls: list[dict] = []
    report = serve_calls(job / "calls", document, calls, before)
    text = (json.dumps(report) + "\n").encode("utf-8")
    # The parent recorded this file's identity. Writing to the open file keeps
    # that identity. Opening the path is only the fallback for a direct caller.
    if record_fd is None:
        descriptor = open_inplace(record_path)
    else:
        descriptor = _adopt_record(record_fd)
    try:
        _write_all(descriptor, text)
    finally:
        os.close(descriptor)
    return 0


def script_document(document, calls: list[dict]):
    """Run calls in this process, for tests. The proxy does not hold ``document``."""
    import queue
    import tempfile
    import threading
    import weakref

    from aicad.thread_stack import prepare_geometry_thread_stack

    # The calls run on this thread, including geometry.
    prepare_geometry_thread_stack()
    pending: queue.Queue[Path | None] = queue.Queue()
    before: dict = {}
    if hasattr(document, "session_payload"):
        try:
            before = {row["name"]: row for row in document.session_payload().get("parts", [])}
        except Exception:
            before = {}

    live: dict = {}

    def run() -> None:
        while True:
            folder = pending.get()
            if folder is None:
                return
            try:
                _serve_one(folder, document, calls, before, live)
            except Exception:
                continue

    threading.Thread(target=run, name="aicad-design-calls", daemon=True).start()

    def call_directory() -> Path:
        folder = Path(tempfile.mkdtemp(prefix="aicad-calls-"))
        pending.put(folder)
        return folder

    proxy, _finish = design_proxy(live=live, call_directory=call_directory)
    weakref.finalize(proxy, pending.put, None)
    return proxy
