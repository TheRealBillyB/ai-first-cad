"""Child process for one sandboxed script. Started by ``aicad.sandbox``.

The limits are applied before build123d is imported. Assistant code never
runs in the parent process.
"""

from __future__ import annotations

import atexit
import io
import json
import os
import sys
import threading
import traceback
from contextlib import redirect_stdout
from pathlib import Path

_CHECKS = frozenset(
    {
        "check_bolts",
        "check_cables",
        "check_clearance",
        "check_fatigue",
        "check_gears",
        "check_packaging",
        "check_print",
        "check_wall_thickness",
        "estimate_power",
        "feature_report",
        "inspect",
        "measure",
        "render",
        "render_animation",
        "render_assembly",
        "run_motion",
        "run_motion_study",
        "section",
        "solve_assembly_modal",
        "solve_gear_train",
        "solve_kinematics",
        "solve_mechanics",
        "solve_modal",
    }
)
_RENDER_LIMIT = 8
# The parent already bounds a script. This is the byte cap for the file itself.
_JOB_MAX_BYTES = 1_000_000
_SCRIPT_MAX_BYTES = 1_000_000
# The same bound the parent uses when it reads result.json.
_RESULT_MAX_BYTES = 512 * 1024 * 1024


def _read_job_text(path: Path, maximum: int) -> str:
    """Read one job file. A link at that name is not followed."""
    from aicad.file_access import read_text

    return read_text(path, maximum=maximum)


def _write(job: Path, payload: dict) -> None:
    """Publish ``result.json``. A link at that name is not followed.

    The text is written with ``Path.write_text`` to a new file of that name.
    The bytes from that file are what get published onto ``result.json``.
    """
    import tempfile

    from aicad.file_access import read_text
    from aicad.paths import publish_text

    text = json.dumps(payload) + "\n"
    staging = Path(tempfile.mkdtemp(prefix=".aicad-result-", dir=job))
    staged = staging / "result.json"
    try:
        staged.write_text(text, encoding="utf-8")
        try:
            written = read_text(staged, maximum=_RESULT_MAX_BYTES)
        except (OSError, UnicodeError):
            written = text
        publish_text(job / "result.json", written, root=job)
    finally:
        staged.unlink(missing_ok=True)
        try:
            staging.rmdir()
        except OSError:
            pass


def _format(code: str, exc: BaseException, filename: str) -> str:
    line_no = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == filename:
            line_no = frame.lineno
    snippet = ""
    lines = code.splitlines()
    if line_no is not None and 1 <= line_no <= len(lines):
        snippet = lines[line_no - 1].strip()
    where = f" on line {line_no}" if line_no is not None else ""
    message = f"Script failed{where}: {type(exc).__name__}: {exc}"
    if snippet:
        message += f"\n    {snippet}"
    return message


def _jsonable(value):
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _jsonable(value.to_dict())
        except Exception:
            return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _allowed_text() -> str:
    from aicad.parity import SCRIPT_METHODS

    return ", ".join(sorted(SCRIPT_METHODS))


def _unavailable(name: str) -> AttributeError:
    return AttributeError(f"{name} is not available in a design script. Allowed calls: {_allowed_text()}.")


def script_document(document, calls: list[dict]) -> "ScriptDocument":
    """The ``document`` name inside a design script.

    The real document and the check list are not stored on the object the
    script holds. Production runs the calls in another process. This helper
    runs them beside the caller for tests.
    """
    from aicad.design_calls import script_document as _script_document

    return _script_document(document, calls)


class ScriptDocument:
    """The ``document`` name inside a design script. Only the public methods are visible."""

    def __init__(self, wrappers: dict):
        object.__setattr__(self, "_wrappers", dict(wrappers))

    def __getattribute__(self, name: str):
        if name.startswith("_"):
            raise _unavailable(name)
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str):
        from aicad.parity import SCRIPT_METHODS

        if name.startswith("_") or name not in SCRIPT_METHODS:
            raise _unavailable(name)
        wrappers = object.__getattribute__(self, "_wrappers")
        try:
            return wrappers[name]
        except KeyError:
            raise _unavailable(name) from None


def _probe(workspace: Path, job: Path) -> int:
    import socket

    from aicad.sandbox import denied_probe_label

    try:
        spec = json.loads(_read_job_text(job / "job.json", _JOB_MAX_BYTES))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _write(job, {"ok": False, "error": "The script job could not be read."})
        return 0
    read_path = str(spec.get("probe_read") or "/etc/passwd")
    write_path = str(spec.get("probe_write") or "/tmp/aicad-sandbox-outside")
    found: dict[str, str] = {}
    try:
        open(read_path, encoding="utf-8").read(1)
        found["read_outside"] = "opened"
    except OSError as exc:
        found["read_outside"] = denied_probe_label(exc)
    try:
        open(write_path, "w", encoding="utf-8").write("x")
        found["write_outside"] = "wrote"
    except OSError as exc:
        found["write_outside"] = denied_probe_label(exc)
    inside = workspace / "probe_inside.txt"
    inside.write_text("ok", encoding="utf-8")
    found["write_inside"] = inside.read_text(encoding="utf-8")
    stream = None
    try:
        stream = socket.socket()
        stream.settimeout(2)
        stream.connect(("1.1.1.1", 80))
        found["tcp"] = "connected"
    except OSError as exc:
        found["tcp"] = denied_probe_label(exc)
    finally:
        if stream is not None:
            stream.close()

    def datagram(connected: bool) -> str:
        try:
            packet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError as exc:
            return denied_probe_label(exc)
        packet.settimeout(2)
        try:
            if connected:
                # connect() only selects the destination. send() is the attempt.
                packet.connect(("1.1.1.1", 53))
                packet.send(b"aicad-net-probe")
            else:
                # Bind first. A blocked implicit bind is WSAEINVAL, not a send.
                packet.bind(("0.0.0.0", 0))
                packet.sendto(b"aicad-net-probe", ("1.1.1.1", 53))
            return "sent"
        except OSError as exc:
            return denied_probe_label(exc)
        finally:
            packet.close()

    found["udp"] = datagram(False)
    found["udp_connected"] = datagram(True)
    if sys.platform == "win32":
        from aicad.sandbox import _windows_net_probe_text

        found["network"] = _windows_net_probe_text
        found["mechanism"] = "appcontainer+job"
    elif sys.platform == "darwin":
        found["mechanism"] = "seatbelt"
    elif sys.platform.startswith("linux"):
        found["mechanism"] = "landlock+seccomp"
    else:
        found["mechanism"] = sys.platform
    _write(job, {"ok": True, "probe": found})
    return 0


def _read_script(job: Path) -> str:
    return _read_job_text(job / "script.py", _SCRIPT_MAX_BYTES)


def _part(workspace: Path, job: Path) -> int:
    try:
        code = _read_script(job)
    except (OSError, UnicodeError):
        _write(job, {"ok": False, "error": "The script could not be read."})
        return 0
    from aicad.kernel import _execute_part_script, export_step_file

    # A script can fork. Only the process that started the script publishes
    # the result. The forked process must not replace that result.
    owner = os.getpid()
    try:
        shape = _execute_part_script(code)
        step = job / "part.step"
        export_step_file(shape, step)
    except Exception as exc:
        if os.getpid() != owner:
            os._exit(1)
        message = str(exc) if str(exc) else _format(code, exc, "<aicad-script>")
        _write(job, {"ok": False, "error": message})
        return 0
    if os.getpid() != owner:
        os._exit(0)
    _write(job, {"ok": True, "step": str(step), "features": shape.feature_report().to_dict()})
    return 0


def _design(workspace: Path, job: Path) -> int:
    from aicad.design_calls import design_proxy
    from aicad.sandbox import STDOUT_MAX, script_builtins

    try:
        code = _read_script(job)
    except (OSError, UnicodeError):
        _write(job, {"ok": False, "error": "The script could not be read."})
        return 0
    proxy, finish = design_proxy(job / "calls")
    namespace = {
        "__builtins__": script_builtins(),
        "__name__": "aicad_design_script",
        "document": proxy,
        "workspace": str(workspace),
    }
    captured = io.StringIO()
    try:
        with redirect_stdout(captured):
            compiled = compile(code, "<aicad-design>", "exec")
            exec(compiled, namespace)  # noqa: S102
        ok = True
        error = None
    except Exception as exc:
        ok = False
        error = str(exc) if isinstance(exc, Exception) and exc.__class__.__module__ == "aicad.errors" else _format(code, exc, "<aicad-design>")
        if not str(exc):
            error = _format(code, exc, "<aicad-design>")
    try:
        finish()
    except Exception as exc:
        ok = False
        finish_error = str(exc) or type(exc).__name__
        error = finish_error if error is None else f"{error}\n{finish_error}"
    errors = [error] if error else []
    stdout = captured.getvalue()
    if len(stdout) > STDOUT_MAX:
        stdout = stdout[:STDOUT_MAX] + "\n... output truncated ..."
    # Checks, pictures, and changed parts are recorded by the process that ran
    # the calls. This file does not supply them.
    _write(
        job,
        {
            "ok": ok,
            "error": error,
            "errors": errors,
            "changed_parts": [],
            "deleted_parts": [],
            "checks": [],
            "stdout": stdout,
            "views": [],
        },
    )
    return 0


def _leave(code: int) -> int:
    """On Windows, skip interpreter shutdown.

    A confined process that has loaded the geometry libraries can take more
    than a minute to exit, and the parent waits for that. The result file is
    already written. macOS keeps the normal exit so the seatbelt wrapper is
    reaped with the child; a direct exit can leave that wrapper running.
    Children that have already exited are collected first so their writes
    stay in the quota.
    """

    _collect_descendant_writes()
    if sys.platform == "win32":
        os._exit(code)
    return code


def _os_thread_count() -> int:
    try:
        return len(os.listdir("/proc/self/task"))
    except OSError:
        return 1


def _load_sandbox():
    """Import the sandbox before the geometry libraries.

    Importing the package runs those libraries and starts their threads.
    Threads started before confinement do not inherit it. Load the sandbox
    module first, confine the process, then import the package.
    """
    import types

    package = Path(__file__).resolve().parent
    preloaded = False
    if "aicad" not in sys.modules:
        stub = types.ModuleType("aicad")
        stub.__path__ = [str(package)]
        stub.__package__ = "aicad"
        stub.__file__ = str(package / "__init__.py")
        sys.modules["aicad"] = stub
        preloaded = True
    from aicad.sandbox import _THREAD_CONFINE_ERROR, apply_sandbox, install_python_guard

    return preloaded, _THREAD_CONFINE_ERROR, apply_sandbox, install_python_guard


def _import_package(preloaded: bool) -> None:
    """Load the package after confinement so its threads inherit the limits."""
    if not preloaded:
        return
    sys.modules.pop("aicad", None)
    import aicad  # noqa: F401


def _early_thread_stack():
    """The stack helper, without importing the geometry package.

    Importing ``aicad`` starts geometry threads. Those threads have to start
    after confinement. This loads the helper by file so the child can set the
    Python thread stack, and can refuse a main thread that is too small,
    before that import.
    """

    import importlib.util

    path = Path(__file__).resolve().parent / "thread_stack.py"
    spec = importlib.util.spec_from_file_location("_aicad_thread_stack_early", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_child_bytes: dict[int, int] = {}
_child_bytes_lock = threading.Lock()
_quota_job: list[Path | None] = [None]
_real_waitpid = os.waitpid
_wait_accounting_installed = False


def _publish_child_writes() -> None:
    """Write the bytes recorded for collected children where the parent can read them."""
    job = _quota_job[0]
    if job is None or sys.platform != "darwin":
        return
    with _child_bytes_lock:
        payload = {str(pid): int(amount) for pid, amount in _child_bytes.items()}
    if not payload:
        return
    from aicad.paths import publish_text

    try:
        publish_text(job / "child-writes.json", json.dumps(payload) + "\n", root=job)
    except OSError:
        return


def _record_zombie_children() -> list[int]:
    """Logical writes of children that have exited and have not been collected.

    macOS keeps that counter until the child is collected. Reading it first
    is what makes a child that exits before the parent opens a counter count.
    """
    if sys.platform != "darwin":
        return []
    from aicad.sandbox_macos import bytes_written, child_pids, process_runtime

    listed = child_pids(os.getpid())
    if not listed:
        return []
    recorded: list[int] = []
    for pid in listed:
        runtime = process_runtime(pid)
        if runtime is None or not runtime[1]:
            continue
        with _child_bytes_lock:
            if pid not in _child_bytes:
                value = bytes_written(pid)
                if value is not None:
                    _child_bytes[pid] = int(value)
        recorded.append(pid)
    return recorded


def _pending_child(pid: int) -> int | None:
    """A child that has exited and has not been collected, without collecting it."""
    waitid = getattr(os, "waitid", None)
    exited = getattr(os, "WEXITED", None)
    nohang = getattr(os, "WNOHANG", None)
    nowait = getattr(os, "WNOWAIT", None)
    if waitid is None or None in (exited, nohang, nowait):
        return None
    which = os.P_PID if pid > 0 else getattr(os, "P_ALL", None)
    if which is None:
        return None
    try:
        status = waitid(which, pid if pid > 0 else 0, exited | nohang | nowait)
    except (AttributeError, ChildProcessError, OSError):
        return None
    if status is None:
        return None
    found = getattr(status, "si_pid", None)
    if isinstance(found, int) and found > 0:
        return found
    return pid if pid > 0 else None


def _remember_child(pid: int) -> None:
    """Record one child's logical writes while it can still be read."""
    with _child_bytes_lock:
        if pid in _child_bytes:
            return
    from aicad.sandbox_macos import bytes_written

    value = bytes_written(pid)
    if value is None:
        return
    with _child_bytes_lock:
        _child_bytes.setdefault(pid, int(value))


def _install_wait_accounting() -> None:
    """Read a child's logical writes before a Python wait collects it."""
    global _wait_accounting_installed
    if _wait_accounting_installed or sys.platform != "darwin":
        return
    _wait_accounting_installed = True

    def waitpid(pid: int, options: int = 0):
        nohang = bool(options & os.WNOHANG)
        while True:
            pending = _pending_child(pid)
            if pending is not None:
                _remember_child(pending)
                _publish_child_writes()
                return _real_waitpid(pending, 0)
            zombies = _record_zombie_children()
            chosen = [item for item in zombies if pid <= 0 or item == pid]
            if chosen:
                _publish_child_writes()
                return _real_waitpid(chosen[0], 0)
            try:
                got = _real_waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return (0, 0) if nohang else _real_waitpid(pid, options)
            if got[0] != 0 or nohang:
                return got
            import time

            time.sleep(0.002)

    os.waitpid = waitpid  # type: ignore[assignment]
    if hasattr(os, "wait"):

        def wait():
            return waitpid(-1, 0)

        os.wait = wait  # type: ignore[assignment]


def _collect_descendant_writes() -> None:
    """Count children that exited before their own write counter was opened.

    On Linux, collecting a child adds its logical writes to this process.
    The parent reads this process before collecting it. On macOS each exited
    child is read while it is still a zombie, and that total is published
    for the parent.
    """
    if sys.platform.startswith("linux"):
        while True:
            try:
                pid, _status = _real_waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            except OSError:
                return
            if pid == 0:
                return
    if sys.platform != "darwin":
        return
    zombies = _record_zombie_children()
    _publish_child_writes()
    for pid in zombies:
        try:
            _real_waitpid(pid, os.WNOHANG)
        except OSError:
            pass


def _wait_for_quota_hold() -> None:
    """Block until the parent has opened the write counter.

    The script has not run yet. Bytes written after this returns are the
    script's. A missing descriptor, or a closed pipe, means there is nothing
    to wait for.
    """
    raw = os.environ.pop("AICAD_QUOTA_HOLD", "")
    if raw.strip() == "":
        return
    try:
        held = int(raw)
    except ValueError:
        return
    if held < 0:
        return
    try:
        while True:
            try:
                os.read(held, 1)
            except InterruptedError:
                continue
            break
    except OSError:
        pass
    try:
        os.close(held)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    _wait_for_quota_hold()
    _install_wait_accounting()
    atexit.register(_collect_descendant_writes)
    stack = _early_thread_stack()
    if stack is not None:
        stack.prepare_geometry_thread_stack()
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--model" and len(args) in (3, 4):
        if stack is not None and not stack.current_thread_can_run_geometry():
            print(stack.SMALL_CALLER_STACK, file=sys.stderr)
            return 1
        from aicad.design_calls import model_main

        record_fd = None
        if len(args) == 4:
            try:
                record_fd = int(args[3])
            except ValueError:
                print("usage: python -P -m aicad.sandbox_run --model JOB RECORD FD", file=sys.stderr)
                return 2
            if record_fd < 0:
                return 2
        return model_main(Path(args[1]), Path(args[2]), record_fd)
    if len(args) != 1:
        print("usage: python -P -m aicad.sandbox_run JOB_DIR", file=sys.stderr)
        return 2
    job = Path(args[0])
    _quota_job[0] = job
    if stack is not None and not stack.current_thread_can_run_geometry():
        try:
            _write(job, {"ok": False, "error": stack.SMALL_CALLER_STACK})
        except Exception:
            print(stack.SMALL_CALLER_STACK, file=sys.stderr)
        return _leave(0)
    try:
        spec = json.loads(_read_job_text(job / "job.json", _JOB_MAX_BYTES))
    except (OSError, UnicodeError, json.JSONDecodeError):
        try:
            _write(job, {"ok": False, "error": "The script job could not be read."})
        except OSError:
            print("The script job could not be read.", file=sys.stderr)
        return _leave(0)
    if not isinstance(spec, dict):
        _write(job, {"ok": False, "error": "The script job could not be read."})
        return _leave(0)
    workspace = Path(os.environ["AICAD_WORKSPACE"]).resolve()
    try:
        import faulthandler

        faulthandler.enable(all_threads=True)
    except (OSError, ValueError):
        pass
    os.environ["AICAD_SANDBOX_CHILD"] = "1"
    os.chdir(workspace)
    # A thread that exists before this package is imported did not come from
    # the geometry libraries. -m imports the package first; those library
    # threads are handled by installing the filter on every thread.
    package_loaded = "aicad" in sys.modules
    early_threads = (not package_loaded) and _os_thread_count() > 1
    try:
        preloaded, _THREAD_CONFINE_ERROR, apply_sandbox, install_python_guard = _load_sandbox()
        if preloaded and _os_thread_count() > 1:
            early_threads = True

        try:
            # The job file names the script kind and the network switch.
            # It does not choose the Landlock version.
            roots = apply_sandbox(
                workspace,
                network=bool(spec.get("network")),
                extra_thread=early_threads,
                result_path=job / "result.json",
            )
        except Exception as exc:
            if str(exc) == _THREAD_CONFINE_ERROR:
                return _leave(0)
            _write(job, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return _leave(0)
        _import_package(preloaded)
        if sys.platform.startswith("linux"):
            from aicad.sandbox import _set_limits

            _set_limits()
        kind = spec.get("kind")
        try:
            if kind == "probe":
                return _leave(_probe(workspace, job))
            install_python_guard(workspace, roots)
            if sys.platform == "win32":
                # The quota watch holds files it counts and lists the tree
                # while this script removes them. Drop the name immediately
                # so the folder can be removed, and leave the watch's read
                # and its scan in place.
                from aicad.file_access import install_name_removal

                install_name_removal()
            if kind == "part":
                return _leave(_part(workspace, job))
            if kind == "design":
                return _leave(_design(workspace, job))
            _write(job, {"ok": False, "error": f"Unknown script job {kind!r}."})
            return _leave(0)
        except Exception as exc:
            _write(job, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return _leave(0)
    except Exception as exc:
        # Importing the geometry stack can fail before the handlers above run.
        # A partial native import then hangs while the process shuts down, and
        # the parent waits out the wall-clock limit. Record the error and leave.
        detail = f"{type(exc).__name__}: {exc}"
        try:
            _write(job, {"ok": False, "error": detail})
        except Exception:
            print(detail, file=sys.stderr)
        _collect_descendant_writes()
        os._exit(1)


if __name__ == "__main__":
    raise SystemExit(main())
