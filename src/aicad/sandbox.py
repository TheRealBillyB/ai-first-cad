"""Run an assistant's Python in a separate process that cannot leave the project.

Linux confines the process with Landlock (files, TCP, and signals) and a small
seccomp filter (every other socket, and starting a new session). The process
cap counts only that script's threads: a cgroup ``pids.max`` when this user can
create one, and otherwise the parent stops the script's process tree. It is
not the user-wide process limit, which also counts a browser and was stalling
geometry. macOS uses ``sandbox-exec`` and ``setrlimit`` for CPU time. The
parent watches the script's resident memory and stops it above the memory cap,
because the address-space cap counts a system library cache and was stopping
every script. Windows uses an AppContainer and a Job Object. CPU time, memory,
and a wall-clock limit are set on that process. If those limits cannot be
applied, the script is not run.

The person turns design scripts on for one project. Opening a project, or
reading a note, does not turn them on. Part scripts, the ones that assign
``part``, use the same confined process so an assistant cannot skip it.
The project folder is not placed on the child interpreter's import path, so
a module shipped in a shared project cannot run before those limits.
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
import threading
import time
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

# Windows starts the child through this snippet. ``python -m`` imports aicad
# before main() runs, so an import error never reaches the child's handler.
# A partial native import can then hang until the wall-clock limit.
_WINDOWS_CHILD_BOOT = r"""
import json, os, sys, traceback
from pathlib import Path
job = Path(sys.argv[1])
try:
    from aicad.sandbox_run import main
except Exception as exc:
    try:
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        (job / "result.json").write_text(
            json.dumps({"ok": False, "error": detail}) + "\n",
            encoding="utf-8",
        )
    except Exception:
        traceback.print_exc()
    os._exit(1)
raise SystemExit(main([str(job)]))
"""

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
# setsid and setpgid. A grandchild that starts a new session leaves the group
# the parent kills when the tool returns. Classic seccomp BPF has no counter,
# so this filter cannot limit how often clone or fork is called. The process
# cap is the cgroup pids controller, or the parent counting this tree's threads.
_ESCAPE_SYSCALLS = {
    "x86_64": (112, 109),
    "aarch64": (157, 154),
}
# Refused on every run, including when the network is on. io_uring can issue a
# socket call this filter would not see. ptrace, cross-process memory, and
# pidfd_getfd reach a process outside the script.
_ISOLATION_SYSCALLS = {
    "x86_64": (
        101,  # ptrace
        310,  # process_vm_readv
        311,  # process_vm_writev
        425,  # io_uring_setup
        426,  # io_uring_enter
        427,  # io_uring_register
        438,  # pidfd_getfd
    ),
    "aarch64": (
        117,  # ptrace
        270,  # process_vm_readv
        271,  # process_vm_writev
        425,  # io_uring_setup
        426,  # io_uring_enter
        427,  # io_uring_register
        438,  # pidfd_getfd
    ),
}
# seccomp_data.arch. A 32-bit int 0x80 call on x86_64 is a different architecture
# and a different syscall number, so a filter that only compares the native
# number would let it through. x32 numbers set bit 30 on this same architecture.
_AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
_X32_SYSCALL_BIT = 0x40000000
# How often the macOS parent checks the script's resident memory.
_MEMORY_POLL_SECONDS = 0.25
_LANDLOCK_SCOPE_SIGNAL = 1 << 1
# Threads in this script only. RLIMIT_NPROC counts every thread of the user,
# so a fixed 256 was stopping geometry on a desktop that already had a browser open.
_PROCESS_CAP = 256
_PR_SET_CHILD_SUBREAPER = 36
_PROCESS_LIMIT = "The script was stopped because it started too many processes."

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


class _RulesetAttrScoped(ctypes.Structure):
    """Landlock ABI 6 adds a scope so the script cannot signal other processes."""

    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


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
    if sys.platform == "darwin":
        from aicad.sandbox_macos import availability_error

        reason = availability_error()
        return (True, "") if reason is None else (False, reason)
    if sys.platform == "win32":
        from aicad.sandbox_windows import availability_error

        reason = availability_error(_read_roots())
        return (True, "") if reason is None else (False, reason)
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
    from aicad.sandbox_state import in_sandbox

    if not in_sandbox():
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


def _cgroup_words(path: Path) -> set[str]:
    try:
        return set(path.read_text(encoding="utf-8").split())
    except OSError:
        return set()


def _own_cgroup() -> Path | None:
    """This process's cgroup v2 directory, or None when cgroup v2 is not in use."""
    try:
        text = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or parts[0] != "0" or parts[1] != "":
            continue
        relative = parts[2].strip()
        if not relative.startswith("/"):
            return None
        if relative == "/":
            return Path("/sys/fs/cgroup")
        return Path("/sys/fs/cgroup") / relative.lstrip("/")
    return None


def _delegate_pids(parent: Path) -> bool:
    """Turn on the pids controller for children of ``parent``. No limit is set here."""
    if "pids" not in _cgroup_words(parent / "cgroup.controllers"):
        return False
    if "pids" in _cgroup_words(parent / "cgroup.subtree_control"):
        return True
    try:
        with open(parent / "cgroup.subtree_control", "a", encoding="utf-8") as handle:
            handle.write("+pids")
    except OSError:
        return "pids" in _cgroup_words(parent / "cgroup.subtree_control")
    return "pids" in _cgroup_words(parent / "cgroup.subtree_control")


def _prepare_pids_cgroup() -> Path | None:
    """A sibling cgroup with ``pids.max``, or None when that needs root.

    The limit has to be a sibling. Setting it on this process's own cgroup
    would cap the whole program, and a child of a cgroup that still holds
    processes cannot take the pids controller.
    """
    if not sys.platform.startswith("linux"):
        return None
    own = _own_cgroup()
    if own is None or own == Path("/sys/fs/cgroup"):
        return None
    parent = own.parent
    if not _delegate_pids(parent):
        return None
    target = parent / f"aicad-{uuid.uuid4().hex}"
    try:
        target.mkdir(mode=0o755)
    except OSError:
        return None
    try:
        (target / "pids.max").write_text(f"{_PROCESS_CAP}\n", encoding="utf-8")
    except OSError:
        _release_pids_cgroup(target)
        return None
    if not os.access(target / "cgroup.procs", os.W_OK):
        _release_pids_cgroup(target)
        return None
    return target


def _move_into_pids_cgroup(cgroup: Path, pid: int) -> bool:
    try:
        (cgroup / "cgroup.procs").write_text(f"{pid}\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _join_pids_cgroup() -> None:
    """Move this process into the cgroup the parent prepared, before Landlock."""
    raw = os.environ.get("AICAD_PIDS_CGROUP")
    if not raw:
        return
    _move_into_pids_cgroup(Path(raw), os.getpid())


def _release_pids_cgroup(path: Path | None) -> None:
    if path is None:
        return
    procs = path / "cgroup.procs"
    for _ in range(25):
        try:
            text = procs.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.split():
            try:
                _kill_pid(int(line))
            except ValueError:
                continue
        try:
            path.rmdir()
            return
        except OSError:
            time.sleep(0.04)


def _adopt_orphans() -> None:
    """Keep a grandchild whose parent exited inside this sandbox, so it can be killed with it."""
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = _libc()
        if hasattr(libc, "prctl"):
            libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    except (OSError, AttributeError):
        return


def _task_count(pids: set[int]) -> int:
    """Threads in these processes. A missing /proc entry counts as nothing."""
    total = 0
    for pid in pids:
        try:
            total += len(os.listdir(f"/proc/{pid}/task"))
        except OSError:
            continue
    return total


def _set_limits() -> None:
    import resource

    memory = memory_bytes()
    cpu = cpu_seconds()
    # macOS maps a multi-gigabyte system library cache into every process.
    # RLIMIT_AS counts that cache, so a 4 GiB cap stops the script before it runs.
    if sys.platform != "darwin":
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        except (OSError, ValueError) as exc:
            raise ScriptError("Could not set the memory limit, so the script was not run.") from exc
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_BYTES, FILE_BYTES))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError) as exc:
        raise ScriptError("Could not set the CPU or file limit, so the script was not run.") from exc


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
    abi = int(libc.syscall(create, None, 0, 1))
    if abi >= 6:
        attr = _RulesetAttrScoped(_HANDLED_FS, net, _LANDLOCK_SCOPE_SIGNAL)
    else:
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


def _block_sockets(*, network: bool) -> None:
    arch = _arch()
    if arch is None:
        raise ScriptError(sandbox_supported()[1])
    numbers = list(_ESCAPE_SYSCALLS[arch])
    numbers.extend(_ISOLATION_SYSCALLS[arch])
    if not network:
        numbers.extend(_NET_SYSCALLS[arch])
    # Refuse a different instruction set, then the listed native numbers.
    bpf_ld = 0x00 | 0x00 | 0x20
    bpf_jeq = 0x05 | 0x10 | 0x00
    bpf_jset = 0x05 | 0x40 | 0x00
    bpf_ret = 0x06
    allow = 0x7FFF0000
    errno_eperm = 0x00050000 | 1
    program = [
        _SockFilter(bpf_ld, 0, 0, 4),
        _SockFilter(bpf_jeq, 1, 0, _AUDIT_ARCH[arch]),
        _SockFilter(bpf_ret, 0, 0, errno_eperm),
        _SockFilter(bpf_ld, 0, 0, 0),
    ]
    if arch == "x86_64":
        program.append(_SockFilter(bpf_jset, 0, 1, _X32_SYSCALL_BIT))
        program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
    for number in numbers:
        program.append(_SockFilter(bpf_jeq, 0, 1, number))
        program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
    program.append(_SockFilter(bpf_ret, 0, 0, allow))
    filters = (_SockFilter * len(program))(*program)
    prog = _SockFprog(len(program), filters)
    libc = _libc()
    if libc.syscall(_SECCOMP[arch], 1, 0, ctypes.byref(prog)) != 0:
        raise ScriptError(
            "Could not apply the script's process limits, so the script was not run."
        )


def confined_errno(error: OSError) -> bool:
    """True when this error is the operating system refusing the sandbox, not a timeout."""
    winerror = getattr(error, "winerror", None)
    if winerror in {5, 10013, 10051}:
        return True
    return error.errno in {1, 13, 101}


def denied_probe_label(error: OSError) -> str:
    code = error.errno
    winerror = getattr(error, "winerror", None)
    if code not in {1, 13, 101} and winerror in {5, 10013, 10051}:
        code = 13 if winerror == 5 else winerror
    return f"denied:{code}"


def _file_blocked(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            handle.read(1)
    except FileNotFoundError:
        return False
    except OSError as exc:
        return confined_errno(exc)
    return False


def _network_blocked() -> bool:
    import socket

    tcp = socket.socket()
    tcp.settimeout(2)
    try:
        tcp.connect(("1.1.1.1", 443))
    except OSError as exc:
        tcp_blocked = confined_errno(exc)
    else:
        tcp_blocked = False
    finally:
        tcp.close()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp.sendto(b"x", ("1.1.1.1", 53))
    except OSError as exc:
        udp_blocked = confined_errno(exc)
    else:
        udp_blocked = False
    finally:
        udp.close()
    return tcp_blocked and udp_blocked


def _refuse_foreign_kill() -> None:
    """Refuse os.kill of any process except this one.

    Linux blocks those signals in the kernel. macOS and Windows do not always
    report signal 0 as a denied signal, so the child also refuses it here.
    """
    if sys.platform == "linux" or sys.platform.startswith("linux"):
        return
    real_kill = os.kill

    def kill(pid, sig):
        try:
            target = int(pid)
        except (TypeError, ValueError):
            target = -1
        own = os.getpid()
        if target not in {own, 0, -own}:
            raise PermissionError(1, "Operation not permitted")
        return real_kill(pid, sig)

    os.kill = kill  # type: ignore[method-assign]


def apply_sandbox(workspace: Path, network: bool) -> list[Path]:
    """Confine the current process. Called only in the script's child process."""
    from aicad.plugins import seal_sandbox_plugins
    from aicad.sandbox_state import mark_sandboxed

    mark_sandboxed()
    seal_sandbox_plugins()
    _refuse_foreign_kill()
    workspace = workspace.resolve()
    temporary = workspace / ".aicad" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(temporary)
    os.environ["TMP"] = str(temporary)
    os.environ["TEMP"] = str(temporary)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    roots = _read_roots()
    if sys.platform == "win32":
        from aicad.sandbox_windows import identity_failures

        failures = identity_failures(memory_bytes())
        if failures:
            raise ScriptError(
                "Windows did not confine the script ("
                + "; ".join(failures)
                + "), so the script was not run."
            )
        if not network and not _network_blocked():
            raise ScriptError("Windows did not turn the network off, so the script was not run.")
        return roots
    if sys.platform == "darwin":
        _set_limits()
        if not _file_blocked("/etc/passwd"):
            raise ScriptError(
                "macOS did not confine the script to the project folder, so the script was not run."
            )
        if not network and not _network_blocked():
            raise ScriptError("macOS did not turn the network off, so the script was not run.")
        return roots
    ok, reason = sandbox_supported()
    if not ok:
        raise ScriptError(reason)
    _adopt_orphans()
    _join_pids_cgroup()
    _set_limits()
    _landlock(workspace, network=network, read_roots=roots)
    _block_sockets(network=network)
    return roots


def _without_project_pythonpath(workspace: Path, raw: str) -> str:
    """Absolute import entries that are not this project.

    A relative entry is resolved in the parent, then stored as an absolute
    path. The child uses the project as its working directory, and a relative
    entry would be resolved there.
    """
    root = workspace.resolve()
    kept: list[str] = []
    for entry in raw.split(os.pathsep):
        if entry == "" or entry == ".":
            continue
        try:
            resolved = Path(entry).expanduser().resolve()
        except OSError:
            continue
        if resolved == root or _inside(resolved, root):
            continue
        kept.append(str(resolved))
    return os.pathsep.join(kept)


def _child_env(workspace: Path, job: Path, network: bool) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(part in upper for part in _SECRET_PARTS):
            continue
        if key in ("AICAD_CONFIG", "AICAD_SETTINGS", "AICAD_PLUGIN_CONSENT", "XDG_CONFIG_HOME"):
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
    if sys.platform == "win32":
        # ezdxf and matplotlib otherwise stat the real user profile. An
        # AppContainer is denied that path. Do not redirect USERPROFILE:
        # that made every script fail with STATUS_INVALID_HANDLE.
        # The font list is built once in the Python install. The other
        # caches stay in the project, which the container can write.
        cache = str(workspace / ".aicad" / "cache")
        font_home = _windows_font_cache_home()
        font_file = font_home / "ezdxf" / "font_manager_cache.json"
        if font_file.is_file() and font_file.stat().st_size > 0:
            env["XDG_CACHE_HOME"] = str(font_home)
        else:
            env["XDG_CACHE_HOME"] = cache
        env["XDG_CONFIG_HOME"] = cache
        env["XDG_DATA_HOME"] = cache
        env["MPLCONFIGDIR"] = cache
    # -P on the command does the same thing. The variable covers a child that
    # starts another interpreter. A module in the project must not be imported
    # while the process is still unrestricted.
    env["PYTHONSAFEPATH"] = "1"
    env.pop("PYTHONSTARTUP", None)
    raw_path = env.get("PYTHONPATH")
    if raw_path is not None:
        cleaned = _without_project_pythonpath(workspace, raw_path)
        if cleaned:
            env["PYTHONPATH"] = cleaned
        else:
            env.pop("PYTHONPATH", None)
    return env


def _windows_font_cache_home() -> Path:
    """A font list the parent builds once, so each script does not scan fonts."""

    root = Path(sys.prefix) / "aicad-font-cache"
    target = root / "ezdxf" / "font_manager_cache.json"
    root.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 0:
        return root
    try:
        from ezdxf.fonts.fonts import build_font_manager_cache

        build_font_manager_cache(target)
    except Exception:
        return root
    return root


def _make_probe(workspace: Path) -> tuple[Path, str, str]:
    """A file and a write target outside the project, so the probe cannot mistake a missing path for a block."""
    candidates = [Path(tempfile.gettempdir()).resolve()]
    try:
        candidates.append(Path.home().resolve())
    except OSError:
        pass
    if workspace.parent != workspace:
        candidates.append(workspace.parent)
    base = candidates[0]
    for candidate in candidates:
        if candidate == workspace or _inside(candidate, workspace):
            continue
        if os.access(candidate, os.W_OK):
            base = candidate
            break
    root = Path(tempfile.mkdtemp(prefix="aicad-probe-", dir=base))
    secret = root / "secret.txt"
    secret.write_text("aicad-confinement-probe\n", encoding="utf-8")
    writes = root / "writes"
    writes.mkdir()
    return root, str(secret), str(writes / "escaped.txt")


def _kill_pid(pid: int) -> None:
    if pid <= 1:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _descendants(leader: int) -> set[int]:
    """Pids whose parent is ``leader`` or one of its descendants."""
    if leader <= 1:
        return set()
    parent_of: dict[int, int] = {}
    if sys.platform.startswith("linux"):
        try:
            names = os.listdir("/proc")
        except OSError:
            return set()
        for name in names:
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/status", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if line.startswith("PPid:"):
                            parent_of[int(name)] = int(line.split()[1])
                            break
            except (OSError, ValueError):
                continue
    elif sys.platform == "darwin":
        try:
            completed = subprocess.run(
                ["ps", "-axo", "pid=,ppid="],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return set()
        for line in completed.stdout.splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                parent_of[int(parts[0])] = int(parts[1])
            except ValueError:
                continue
    else:
        return set()
    children: dict[int, list[int]] = {}
    for pid, ppid in parent_of.items():
        children.setdefault(ppid, []).append(pid)
    found: set[int] = set()
    stack = list(children.get(leader, []))
    while stack:
        pid = stack.pop()
        if pid in found or pid <= 1:
            continue
        found.add(pid)
        stack.extend(children.get(pid, []))
    return found


def _tree_resident_bytes(leader: int, children: set[int]) -> int | None:
    """Resident bytes of the script and the children still running under it."""
    from aicad.sandbox_macos import resident_bytes

    leader_size = resident_bytes(leader)
    if leader_size is None:
        return None
    total = leader_size
    for pid in children:
        extra = resident_bytes(pid)
        if extra:
            total += extra
    return total


def _watch_descendants(
    leader: int,
    seen: set[int],
    lock: threading.Lock,
    stop: threading.Event,
    memory_cap: int | None = None,
    memory_hit: threading.Event | None = None,
    process_cap: int | None = None,
    process_hit: threading.Event | None = None,
) -> None:
    """Remember children, kill any that leave the group, and enforce the memory and process caps."""
    next_sample = 0.0
    while not stop.is_set():
        try:
            found = _descendants(leader)
        except OSError:
            found = set()
        escaped: list[int] = []
        for pid in found:
            try:
                if os.getpgid(pid) != leader:
                    escaped.append(pid)
            except OSError:
                continue
        with lock:
            seen.update(found)
        for pid in escaped:
            _kill_pid(pid)
        if process_cap is not None and _task_count({leader, *found}) > process_cap:
            if process_hit is not None:
                process_hit.set()
            _kill_process_tree(leader, seen, lock)
            return
        if memory_cap is not None and time.monotonic() >= next_sample:
            next_sample = time.monotonic() + _MEMORY_POLL_SECONDS
            used = _tree_resident_bytes(leader, found)
            if used is not None and used > memory_cap:
                if memory_hit is not None:
                    memory_hit.set()
                _kill_process_tree(leader, seen, lock)
                return
        stop.wait(0.02)


def _kill_process_tree(leader: int, seen: set[int], lock: threading.Lock) -> None:
    """Kill the script's process group and any child that already left it."""
    try:
        os.killpg(leader, signal.SIGKILL)
    except OSError:
        pass
    _kill_pid(leader)
    with lock:
        targets = set(seen)
    try:
        targets.update(_descendants(leader))
    except OSError:
        pass
    for pid in targets:
        _kill_pid(pid)


def _spawn(workspace: Path, kind: str, script: str | None, network: bool) -> dict:
    ok, reason = sandbox_supported()
    if not ok:
        raise ScriptError(reason)
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    job = workspace / ".aicad" / "jobs" / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=True)
    (workspace / ".aicad" / "tmp").mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # Created before the AppContainer grant so the container can write it.
        (workspace / ".aicad" / "cache").mkdir(parents=True, exist_ok=True)
    spec: dict = {"kind": kind, "network": network}
    probe_root = None
    if kind == "probe":
        probe_root, spec["probe_read"], spec["probe_write"] = _make_probe(workspace)
    (job / "job.json").write_text(json.dumps(spec) + "\n", encoding="utf-8")
    if script is not None:
        (job / "script.py").write_text(script, encoding="utf-8")
    # -P keeps the project folder off sys.path. Without it, python -m imports
    # aicad from that folder before the limits are applied.
    if sys.platform == "win32":
        command = [sys.executable, "-B", "-u", "-P", "-c", _WINDOWS_CHILD_BOOT, str(job)]
    else:
        command = [sys.executable, "-P", "-m", "aicad.sandbox_run", str(job)]
    profile_path: Path | None = None
    if sys.platform == "darwin":
        from aicad.sandbox_macos import build_profile, command_prefix

        profile = build_profile(
            [str(path) for path in _read_roots()],
            [str(workspace)],
            network=network,
        )
        descriptor, name = tempfile.mkstemp(prefix="aicad-seatbelt-", suffix=".sb")
        os.close(descriptor)
        profile_path = Path(name)
        profile_path.write_text(profile, encoding="utf-8")
        command = [*command_prefix(str(profile_path)), *command]
    wall = wall_seconds()
    stop_watch: threading.Event | None = None
    watcher: threading.Thread | None = None
    seen: set[int] = set()
    seen_lock = threading.Lock()
    memory_hit = threading.Event()
    memory_cap = memory_bytes() if sys.platform == "darwin" else None
    process_hit = threading.Event()
    process_cap = _PROCESS_CAP if sys.platform.startswith("linux") else None
    pids_cgroup: Path | None = None
    try:
        if sys.platform == "win32":
            from aicad.sandbox_windows import spawn as windows_spawn

            code, stdout, stderr, timed_out, memory_killed, _mechanism = windows_spawn(
                command,
                cwd=str(workspace),
                env=_child_env(workspace, job, network),
                read_paths=_read_roots(),
                workspace=workspace,
                cpu_seconds=cpu_seconds(),
                memory_bytes=memory_bytes(),
                network=network,
                timeout_seconds=wall,
                stdout_path=job / "stdout.txt",
                stderr_path=job / "stderr.txt",
            )
            if timed_out:
                shutil.rmtree(job, ignore_errors=True)
                raise ScriptError(f"The script ran longer than {wall} seconds, so it was stopped.")
            return _payload_from_result(job, code, stdout, stderr, memory_killed=memory_killed)
        if sys.platform.startswith("linux"):
            pids_cgroup = _prepare_pids_cgroup()
        env = _child_env(workspace, job, network)
        env.pop("AICAD_PIDS_CGROUP", None)
        if pids_cgroup is not None:
            env["AICAD_PIDS_CGROUP"] = str(pids_cgroup)
        proc = subprocess.Popen(
            command,
            cwd=str(workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if pids_cgroup is not None:
            _move_into_pids_cgroup(pids_cgroup, proc.pid)
        stop_watch = threading.Event()
        watcher = threading.Thread(
            target=_watch_descendants,
            args=(
                proc.pid,
                seen,
                seen_lock,
                stop_watch,
                memory_cap,
                memory_hit,
                process_cap,
                process_hit,
            ),
            name="aicad-sandbox-reap",
            daemon=True,
        )
        watcher.start()
        try:
            stdout_b, stderr_b = proc.communicate(timeout=wall)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc.pid, seen, seen_lock)
            proc.communicate()
            shutil.rmtree(job, ignore_errors=True)
            raise ScriptError(f"The script ran longer than {wall} seconds, so it was stopped.") from None
        _kill_process_tree(proc.pid, seen, seen_lock)
        return _payload_from_result(
            job,
            proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode("utf-8", "replace"),
            stderr_b.decode("utf-8", "replace"),
            memory_killed=memory_hit.is_set(),
            process_killed=process_hit.is_set(),
        )
    finally:
        if stop_watch is not None:
            stop_watch.set()
        if watcher is not None:
            watcher.join(timeout=1)
        _release_pids_cgroup(pids_cgroup)
        if profile_path is not None:
            profile_path.unlink(missing_ok=True)
        if probe_root is not None:
            shutil.rmtree(probe_root, ignore_errors=True)


def _payload_from_result(
    job: Path,
    code: int,
    stdout: str,
    stderr: str,
    *,
    memory_killed: bool,
    process_killed: bool = False,
) -> dict:
    if process_killed:
        shutil.rmtree(job, ignore_errors=True)
        raise ScriptError(_PROCESS_LIMIT)
    if memory_killed:
        shutil.rmtree(job, ignore_errors=True)
        raise ScriptError("The script was stopped because it hit the memory or CPU limit.")
    result_path = job / "result.json"
    if not result_path.is_file():
        tail = stderr[-2000:]
        shutil.rmtree(job, ignore_errors=True)
        # SIGKILL is the memory cap and the parent's kill. SIGXCPU is the CPU
        # cap. Other signals, including an abort, are crashes.
        limit_signal = code < 0 and -code in {
            signal.SIGKILL,
            getattr(signal, "SIGXCPU", -1),
        }
        if memory_killed or limit_signal:
            detail = tail.strip()
            if code < 0:
                try:
                    signame = signal.strsignal(-code) or str(-code)
                except (ValueError, OSError):
                    signame = str(-code)
                detail = (detail + f"\nstopped by signal {-code} ({signame})").strip()
            raise ScriptError(
                "The script was stopped because it hit the memory or CPU limit."
                + (f"\n{detail}" if detail else "")
            )
        detail = tail.strip() or stdout[-2000:].strip()
        if code < 0:
            try:
                signame = signal.strsignal(-code) or str(-code)
            except (ValueError, OSError):
                signame = str(-code)
            detail = (detail + f"\nstopped by signal {-code} ({signame})").strip()
        raise ScriptError(
            "The script stopped before it could report a result."
            + (f"\n{detail}" if detail else f"\nexit {code}")
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
