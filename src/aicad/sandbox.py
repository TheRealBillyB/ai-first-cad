"""Run an assistant's Python in a separate process that cannot leave the project.

Linux confines the process with Landlock (files and TCP) and a small seccomp
filter (every other socket). CPU time, memory, and a wall-clock limit are set
on that process. If those limits cannot be applied, the script is not run.

The person turns design scripts on for one project. Opening a project, or
reading a note, does not turn them on. Part scripts, the ones that assign
``part``, use the same confined process so an assistant cannot skip it.
"""

from __future__ import annotations

import builtins
import ctypes
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aicad.errors import ScriptError
from aicad.parity import ALLOWED_IMPORT_ROOTS

WARNING = (
    "A design script runs code the assistant wrote. It can read and change files "
    "in this project folder. It cannot use the network, and it cannot read or write "
    "files outside the project. It stops if it runs too long or uses too much memory. "
    "Turn it on for this project before the first run. Reading a note or opening the "
    "project does not turn it on."
)

# Defaults. A test may set a lower value. Nothing can set a higher one.
WALL_SECONDS = 120
CPU_SECONDS = 60
MEMORY_BYTES = 4 * 1024 * 1024 * 1024
FILE_BYTES = 512 * 1024 * 1024
SCRIPT_MAX_CHARS = 200_000
STDOUT_MAX = 64_000

_REAL_IMPORT = builtins.__import__
_GUARD = False
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

_FS_EXECUTE = 1 << 0
_FS_WRITE = 1 << 1
_FS_READ = 1 << 2
_FS_READ_DIR = 1 << 3
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_DIR = 1 << 7
_FS_MAKE_REG = 1 << 8
_FS_REFER = 1 << 13
_FS_TRUNCATE = 1 << 14
_NET_BIND = 1 << 0
_NET_CONNECT = 1 << 1
_READ_ACCESS = _FS_EXECUTE | _FS_READ | _FS_READ_DIR
_WORK_ACCESS = (
    _FS_READ
    | _FS_WRITE
    | _FS_READ_DIR
    | _FS_REMOVE_DIR
    | _FS_REMOVE_FILE
    | _FS_MAKE_DIR
    | _FS_MAKE_REG
    | _FS_REFER
    | _FS_TRUNCATE
)
_HANDLED_FS = _WORK_ACCESS | _FS_EXECUTE

_LANDLOCK = {"x86_64": (444, 445, 446), "aarch64": (444, 445, 446)}
_SECCOMP = {"x86_64": 317, "aarch64": 277}
_NET_SYSCALLS = {
    "x86_64": (41, 42, 43, 44, 45, 46, 47, 49, 50, 53, 288),
    "aarch64": (198, 199, 200, 201, 202, 203, 206, 207, 211, 212, 242),
}

_SECRET_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
_SAFE_BUILTINS = (
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "EOFError",
    "Exception",
    "ImportError",
    "IndexError",
    "KeyError",
    "LookupError",
    "MemoryError",
    "NameError",
    "NotImplementedError",
    "OSError",
    "OverflowError",
    "RuntimeError",
    "StopIteration",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
    "__build_class__",
    "abs",
    "all",
    "any",
    "bool",
    "bytearray",
    "bytes",
    "chr",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "hasattr",
    "hash",
    "hex",
    "id",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "object",
    "oct",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "zip",
)


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    _pack_ = 1


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(_SockFilter))]


def _arch() -> str | None:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    return None


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def sandbox_supported() -> tuple[bool, str]:
    """Whether this machine can confine a script. The second value is the reason it cannot."""
    if not sys.platform.startswith("linux"):
        return False, (
            "This computer cannot keep a script inside the project folder with the network off, "
            "so the script will not run."
        )
    arch = _arch()
    if arch is None:
        return False, (
            f"This computer ({platform.machine()}) cannot keep a script inside the project folder "
            "with the network off, so the script will not run."
        )
    libc = _libc()
    abi = libc.syscall(_LANDLOCK[arch][0], None, 0, 1)
    if abi < 4:
        errno = ctypes.get_errno()
        return False, (
            "This computer's kernel cannot block both files and the network for a script "
            f"(Landlock ABI {abi}, errno {errno}), so the script will not run."
        )
    return True, ""


def _capped(env_name: str, default: int, ceiling: int) -> int:
    raw = os.environ.get(env_name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, min(value, ceiling))


def wall_seconds() -> int:
    return _capped("AICAD_SCRIPT_WALL_SECONDS", WALL_SECONDS, WALL_SECONDS)


def cpu_seconds() -> int:
    return _capped("AICAD_SCRIPT_CPU_SECONDS", CPU_SECONDS, CPU_SECONDS)


def memory_bytes() -> int:
    megabytes = _capped("AICAD_SCRIPT_MEMORY_MB", MEMORY_BYTES // (1024 * 1024), MEMORY_BYTES // (1024 * 1024))
    return megabytes * 1024 * 1024


def config_dir() -> Path:
    override = os.environ.get("AICAD_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "aicad"


def _opt_in_file() -> Path:
    return config_dir() / "design_scripts.json"


def _read_opt() -> dict:
    path = _opt_in_file()
    if not path.is_file():
        return {"projects": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"projects": {}}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        return {"projects": {}}
    return data


def _write_opt(data: dict) -> None:
    path = _opt_in_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def design_scripts_enabled(workspace: str | Path) -> bool:
    root = str(Path(workspace).resolve())
    entry = _read_opt()["projects"].get(root)
    return isinstance(entry, dict) and isinstance(entry.get("enabled_at"), str)


def allow_design_scripts(workspace: str | Path) -> dict:
    """Record that the person allows design scripts in this project."""
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    data = _read_opt()
    data["projects"][str(root)] = {"enabled_at": _now()}
    _write_opt(data)
    return {
        "enabled": True,
        "workspace": str(root),
        "warning": WARNING,
        "log": str(root / ".aicad" / "script_log.jsonl"),
    }


def warn_design_scripts(workspace: str | Path) -> None:
    """Print the design-script warning on stderr. MCP stdout stays a clean protocol stream."""
    status = design_script_status(workspace)
    print(status["warning"], file=sys.stderr)
    if status["enabled"]:
        print(
            f"Design scripts are on for this project. Network is {status['network']}. Log: {status['log']}",
            file=sys.stderr,
        )
    else:
        print(f"Design scripts are off. Turn them on with: {status['enable']}", file=sys.stderr)


def require_in_project(path: str | Path, workspace: str | Path, *, writing: bool) -> None:
    """Refuse a design-script path that leaves the project. No-op outside the sandbox child."""
    if os.environ.get("AICAD_SANDBOX_CHILD") != "1":
        return
    root = Path(workspace).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    if _inside(candidate, root):
        return
    if not writing and _under(candidate, _read_roots()):
        return
    action = "write" if writing else "read"
    raise ScriptError(
        f"The script tried to {action} {candidate}, which is outside the project folder {root}. "
        "A design script can only use files inside that folder."
    )


def design_script_status(workspace: str | Path, network_once: bool = False, network_all: bool = False) -> dict:
    root = Path(workspace).resolve()
    if network_all:
        network = "on"
    elif network_once:
        network = "next run"
    else:
        network = "off"
    return {
        "enabled": design_scripts_enabled(root),
        "warning": WARNING,
        "enable": f"aicad allow-scripts --workspace {root}",
        "network": network,
        "log": str(root / ".aicad" / "script_log.jsonl"),
    }


def script_log_path(workspace: Path) -> Path:
    return Path(workspace) / ".aicad" / "script_log.jsonl"


def _append_log(workspace: Path, script: str, ok: bool, error: str | None) -> None:
    path = script_log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": _now(), "kind": "design", "ok": ok, "error": error, "script": script}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _check_script(code: str) -> None:
    if not isinstance(code, str) or not code.strip():
        raise ScriptError("The script is empty.")
    if len(code) > SCRIPT_MAX_CHARS:
        raise ScriptError(f"The script is longer than {SCRIPT_MAX_CHARS} characters.")


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Import hook for a design script. Refuses modules that are not on the allowlist."""
    if level and level != 0:
        raise ImportError("Relative imports are not allowed in a design script.")
    root = str(name).split(".", 1)[0]
    if root not in ALLOWED_IMPORT_ROOTS:
        allowed = ", ".join(sorted(ALLOWED_IMPORT_ROOTS))
        raise ImportError(
            f"Import of {name!r} is not allowed in a design script. Allowed modules: {allowed}."
        )
    return _REAL_IMPORT(name, globals, locals, fromlist, level)


def script_builtins() -> dict:
    """The names a design script starts with. No exec, eval, or open-ended import."""
    allowed = {name: getattr(builtins, name) for name in _SAFE_BUILTINS if hasattr(builtins, name)}
    allowed["__import__"] = guarded_import
    allowed["open"] = builtins.open
    return allowed


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _under(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def install_python_guard(workspace: Path, read_roots: list[Path]) -> None:
    """Make ordinary Python file calls fail clearly outside the project.

    Native libraries still use the kernel rules. This wrapper is the message
    a script sees when it calls open() itself.
    """
    global _GUARD
    if _GUARD:
        return
    import io

    real_open = builtins.open
    real_os_open = os.open
    root = workspace.resolve()

    def refuse(path: Path, writing: bool) -> None:
        action = "write" if writing else "read"
        raise ScriptError(
            f"The script tried to {action} {path}, which is outside the project folder {root}. "
            "A design script can only use files inside that folder."
        )

    def py_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            writing = any(flag in str(mode) for flag in ("w", "a", "x", "+"))
            path = Path(os.fsdecode(file))
            if not path.is_absolute():
                path = Path.cwd() / path
            if not _inside(path, root) and (writing or not _under(path, read_roots)):
                refuse(path, writing)
        return real_open(file, mode, *args, **kwargs)

    def os_open(path, flags, mode=0o777, *, dir_fd=None):
        if not isinstance(path, int):
            writing = bool(flags & _WRITE_FLAGS)
            file_path = Path(os.fsdecode(path))
            if not file_path.is_absolute():
                file_path = Path.cwd() / file_path
            if not _inside(file_path, root) and (writing or not _under(file_path, read_roots)):
                refuse(file_path, writing)
        return real_os_open(path, flags, mode, dir_fd=dir_fd)

    builtins.open = py_open
    io.open = py_open  # type: ignore[assignment]
    os.open = os_open  # type: ignore[assignment]
    _GUARD = True


def _read_roots() -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()

    def add(candidate: str | Path) -> None:
        if not candidate:
            return
        path = Path(candidate)
        if not path.exists():
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        found.append(resolved)

    for entry in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/proc", "/dev", "/etc/alternatives"):
        add(entry)
    add(sys.prefix)
    add(sys.base_prefix)
    add(Path(sys.executable).resolve().parent)
    for entry in sys.path:
        if entry:
            add(entry)
    return found


def _set_limits() -> None:
    import resource

    memory = memory_bytes()
    cpu = cpu_seconds()
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_BYTES, FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _landlock(workspace: Path, network: bool, read_roots: list[Path]) -> None:
    arch = _arch()
    if arch is None:
        raise ScriptError(sandbox_supported()[1])
    create, add, restrict = _LANDLOCK[arch]
    libc = _libc()
    if hasattr(libc, "prctl"):
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise ScriptError("Could not lock the script process, so the script was not run.")
    net = 0 if network else (_NET_BIND | _NET_CONNECT)
    attr = _RulesetAttr(_HANDLED_FS, net)
    ruleset = libc.syscall(create, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if ruleset < 0:
        raise ScriptError(
            "Could not confine the script to the project folder, so the script was not run."
        )

    def allow(path: Path, access: int) -> None:
        if not path.exists():
            return
        descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = _PathBeneath(access, descriptor)
            result = libc.syscall(add, ruleset, 1, ctypes.byref(rule), 0)
        finally:
            os.close(descriptor)
        if result < 0:
            raise ScriptError(
                f"Could not allow {path} inside the script sandbox, so the script was not run."
            )

    try:
        allow(workspace, _WORK_ACCESS)
        for root in read_roots:
            allow(root, _READ_ACCESS)
        if Path("/dev/null").exists():
            allow(Path("/dev/null"), _FS_READ | _FS_WRITE)
        result = libc.syscall(restrict, ruleset, 0)
    finally:
        os.close(ruleset)
    if result < 0:
        raise ScriptError(
            "Could not turn on the file and network limits, so the script was not run."
        )


def _block_sockets() -> None:
    arch = _arch()
    if arch is None:
        raise ScriptError(sandbox_supported()[1])
    numbers = _NET_SYSCALLS[arch]
    # Load the syscall number, refuse the network ones, allow everything else.
    bpf_ld = 0x00 | 0x00 | 0x20
    bpf_jeq = 0x05 | 0x10 | 0x00
    bpf_ret = 0x06
    allow = 0x7FFF0000
    errno_eperm = 0x00050000 | 1
    program = [_SockFilter(bpf_ld, 0, 0, 0)]
    for number in numbers:
        program.append(_SockFilter(bpf_jeq, 0, 1, number))
        program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
    program.append(_SockFilter(bpf_ret, 0, 0, allow))
    filters = (_SockFilter * len(program))(*program)
    prog = _SockFprog(len(program), filters)
    libc = _libc()
    if libc.syscall(_SECCOMP[arch], 1, 0, ctypes.byref(prog)) != 0:
        raise ScriptError("Could not turn the network off for the script, so the script was not run.")


def apply_sandbox(workspace: Path, network: bool) -> list[Path]:
    """Confine the current process. Called only in the script's child process."""
    ok, reason = sandbox_supported()
    if not ok:
        raise ScriptError(reason)
    workspace = workspace.resolve()
    temporary = workspace / ".aicad" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(temporary)
    os.environ["TMP"] = str(temporary)
    os.environ["TEMP"] = str(temporary)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    roots = _read_roots()
    _set_limits()
    _landlock(workspace, network=network, read_roots=roots)
    if not network:
        _block_sockets()
    return roots


def _child_env(workspace: Path, job: Path, network: bool) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(part in upper for part in _SECRET_PARTS):
            continue
        if key in ("AICAD_CONFIG", "AICAD_SETTINGS"):
            continue
        env[key] = value
    env["AICAD_SANDBOX_CHILD"] = "1"
    env["AICAD_WORKSPACE"] = str(workspace)
    env["AICAD_JOB"] = str(job)
    env["AICAD_NETWORK"] = "1" if network else "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    tmp = str(workspace / ".aicad" / "tmp")
    env["TMPDIR"] = tmp
    env["TMP"] = tmp
    env["TEMP"] = tmp
    return env


def _spawn(workspace: Path, kind: str, script: str | None, network: bool) -> dict:
    ok, reason = sandbox_supported()
    if not ok:
        raise ScriptError(reason)
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    job = workspace / ".aicad" / "jobs" / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=True)
    (workspace / ".aicad" / "tmp").mkdir(parents=True, exist_ok=True)
    (job / "job.json").write_text(json.dumps({"kind": kind, "network": network}) + "\n", encoding="utf-8")
    if script is not None:
        (job / "script.py").write_text(script, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "aicad.sandbox_run", str(job)],
        cwd=str(workspace),
        env=_child_env(workspace, job, network),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    wall = wall_seconds()
    try:
        stdout, stderr = proc.communicate(timeout=wall)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        shutil.rmtree(job, ignore_errors=True)
        raise ScriptError(f"The script ran longer than {wall} seconds, so it was stopped.") from None
    result_path = job / "result.json"
    if not result_path.is_file():
        tail = stderr.decode("utf-8", errors="replace")[-2000:]
        shutil.rmtree(job, ignore_errors=True)
        if proc.returncode is not None and proc.returncode < 0:
            raise ScriptError(
                "The script was stopped because it hit the memory or CPU limit."
                + (f"\n{tail}" if tail.strip() else "")
            )
        detail = tail.strip() or stdout.decode("utf-8", errors="replace")[-2000:].strip()
        raise ScriptError(
            "The script stopped before it could report a result."
            + (f"\n{detail}" if detail else "")
        )
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScriptError(f"The script's result could not be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScriptError("The script's result was not a report.")
    payload["_job"] = str(job)
    return payload


def _drop_job(payload: dict) -> None:
    job = payload.pop("_job", None)
    if job:
        shutil.rmtree(job, ignore_errors=True)


def run_part_script(code: str, workspace: str | Path | None = None):
    """Run a part script (``part = ...``) in the sandbox and return the solid."""
    _check_script(code)
    temporary = None
    if workspace is None:
        temporary = tempfile.TemporaryDirectory(prefix="aicad-script-")
        workspace = Path(temporary.name)
    try:
        payload = _spawn(Path(workspace), "part", code, network=False)
        if not payload.get("ok"):
            raise ScriptError(str(payload.get("error") or "The part script failed."))
        from aicad.kernel import import_step_file
        from aicad.models import feature_report_from_dict

        step = Path(str(payload["step"]))
        shape = import_step_file(step)
        shape.features = feature_report_from_dict(payload.get("features"))
        return shape
    finally:
        if "payload" in locals():
            _drop_job(payload)
        if temporary is not None:
            temporary.cleanup()


def run_design_script(workspace: str | Path, code: str, network: bool = False) -> dict:
    """Run a design script against the project. The person must have allowed it."""
    root = Path(workspace).resolve()
    _check_script(code)
    if not design_scripts_enabled(root):
        status = design_script_status(root)
        raise ScriptError(f"{status['warning']} It is off for this project. Turn it on with: {status['enable']}")
    try:
        payload = _spawn(root, "design", code, network=network)
    except ScriptError as exc:
        _append_log(root, code, False, str(exc))
        raise
    _drop_job(payload)
    error = payload.get("error")
    _append_log(root, code, bool(payload.get("ok")), error if isinstance(error, str) else None)
    return payload


def os_probe(workspace: str | Path) -> dict:
    """Ask a confined process to touch the network and a file outside the project.

    Used by the tests. It does not run assistant code.
    """
    payload = _spawn(Path(workspace), "probe", None, network=False)
    _drop_job(payload)
    probe = payload.get("probe")
    if not isinstance(probe, dict):
        raise ScriptError(str(payload.get("error") or "The sandbox probe did not report."))
    return probe
