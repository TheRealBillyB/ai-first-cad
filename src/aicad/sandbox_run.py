"""Child process for one sandboxed script. Started by ``aicad.sandbox``.

The limits are applied before build123d is imported. Assistant code never
runs in the parent process.
"""

from __future__ import annotations

import io
import json
import os
import sys
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


def _read_job_text(path: Path, maximum: int) -> str:
    """Read one job file. A link at that name is not followed."""
    from aicad.file_access import read_text

    return read_text(path, maximum=maximum)


def _write(job: Path, payload: dict) -> None:
    from aicad.paths import publish_text

    publish_text(job / "result.json", json.dumps(payload) + "\n", root=job)


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

    try:
        shape = _execute_part_script(code)
        step = job / "part.step"
        export_step_file(shape, step)
    except Exception as exc:
        message = str(exc) if str(exc) else _format(code, exc, "<aicad-script>")
        _write(job, {"ok": False, "error": message})
        return 0
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
    """

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


def main(argv: list[str] | None = None) -> int:
    stack = _early_thread_stack()
    if stack is not None:
        stack.prepare_geometry_thread_stack()
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 3 and args[0] == "--model":
        if stack is not None and not stack.current_thread_can_run_geometry():
            print(stack.SMALL_CALLER_STACK, file=sys.stderr)
            return 1
        from aicad.design_calls import model_main

        return model_main(Path(args[1]), Path(args[2]))
    if len(args) != 1:
        print("usage: python -P -m aicad.sandbox_run JOB_DIR", file=sys.stderr)
        return 2
    job = Path(args[0])
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
        os._exit(1)


if __name__ == "__main__":
    raise SystemExit(main())
