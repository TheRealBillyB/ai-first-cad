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
        "solve_kinematics",
        "solve_mechanics",
        "solve_modal",
    }
)
_RENDER_LIMIT = 8


def _write(job: Path, payload: dict) -> None:
    (job / "result.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")


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


class ScriptDocument:
    """The ``document`` name inside a design script. Only the public methods are visible."""

    def __init__(self, document, calls: list[dict]):
        # Stored off the instance so a script cannot read it back by name.
        object.__setattr__(self, "_state", (document, calls, [0]))

    def __getattribute__(self, name: str):
        if name.startswith("_"):
            from aicad.parity import SCRIPT_METHODS

            allowed = ", ".join(sorted(SCRIPT_METHODS))
            raise AttributeError(f"{name} is not available in a design script. Allowed calls: {allowed}.")
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str):
        from aicad.parity import SCRIPT_METHODS

        if name.startswith("_") or name not in SCRIPT_METHODS:
            allowed = ", ".join(sorted(SCRIPT_METHODS))
            raise AttributeError(f"{name} is not available in a design script. Allowed calls: {allowed}.")
        document, calls, depth = object.__getattribute__(self, "_state")
        method = getattr(document, name)

        def wrapped(*args, **kwargs):
            depth[0] += 1
            outer = depth[0] == 1
            try:
                result = method(*args, **kwargs)
            except Exception as exc:
                if outer:
                    calls.append({"method": name, "error": f"{type(exc).__name__}: {exc}"})
                raise
            else:
                if outer:
                    calls.append({"method": name, "result": _jsonable(result)})
                return result
            finally:
                depth[0] -= 1

        return wrapped


def _probe(workspace: Path, job: Path) -> int:
    import socket

    from aicad.sandbox import denied_probe_label

    spec = json.loads((job / "job.json").read_text(encoding="utf-8"))
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


def _part(workspace: Path, job: Path) -> int:
    code = (job / "script.py").read_text(encoding="utf-8")
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
    from aicad.sandbox import STDOUT_MAX, script_builtins

    code = (job / "script.py").read_text(encoding="utf-8")
    from aicad.document import Document

    document = Document(workspace)
    before = {row["name"]: row for row in document.session_payload().get("parts", [])}
    calls: list[dict] = []
    proxy = ScriptDocument(document, calls)
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
    errors = notes[:]
    if error:
        errors.insert(0, error)
    stdout = captured.getvalue()
    if len(stdout) > STDOUT_MAX:
        stdout = stdout[:STDOUT_MAX] + "\n... output truncated ..."
    _write(
        job,
        {
            "ok": ok,
            "error": error,
            "errors": errors,
            "changed_parts": changed_parts,
            "deleted_parts": deleted,
            "checks": checks,
            "stdout": stdout,
            "views": views,
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -P -m aicad.sandbox_run JOB_DIR", file=sys.stderr)
        return 2
    job = Path(args[0])
    spec = json.loads((job / "job.json").read_text(encoding="utf-8"))
    workspace = Path(os.environ["AICAD_WORKSPACE"]).resolve()
    try:
        import faulthandler

        faulthandler.enable(all_threads=True)
    except (OSError, ValueError):
        pass
    os.environ["AICAD_SANDBOX_CHILD"] = "1"
    os.chdir(workspace)
    try:
        from aicad.sandbox import apply_sandbox, install_python_guard

        try:
            # The job file names the script kind and the network switch.
            # It does not choose the Landlock version.
            roots = apply_sandbox(workspace, network=bool(spec.get("network")))
        except Exception as exc:
            _write(job, {"ok": False, "error": f"{exc}\n{traceback.format_exc()}"})
            return _leave(0)
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
        try:
            _write(
                job,
                {"ok": False, "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"},
            )
        except Exception:
            traceback.print_exc()
        os._exit(1)


if __name__ == "__main__":
    raise SystemExit(main())
