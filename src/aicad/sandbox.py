"""Run an assistant's Python in a separate process that cannot leave the project.

Linux confines the process with Landlock (files, TCP, and signals) and a small
seccomp filter. That filter accepts only the native instruction set. It refuses
io_uring, ptrace, and cross-process memory, and it blocks every other socket
and a new session. It also refuses a resource limit aimed at another process,
and a file-owner or terminal notice that can deliver a signal. Landlock also refuses a symlink, named pipe, socket node, or
device node outside the project. The process cap counts only that script's
threads: a cgroup ``pids.max`` when this user can create one, and otherwise the
parent stops the script's process tree. It is not the user-wide process limit,
which also counts a browser and was stalling geometry. macOS uses
``sandbox-exec`` and ``setrlimit`` for CPU time. The parent watches the script's
resident memory and stops it above the memory cap, because the address-space
cap counts a system library cache and was stopping every script. On macOS the
call waits until every descendant has exited, and a listing that fails is not
a clean run. Windows uses
an AppContainer and a Job Object. CPU time, memory, and a wall-clock limit are
set on that process. If those limits cannot be applied, the script is not run.
On Linux the process filter is installed on every thread that already exists.
If another thread is already there, the script is not run.

The person turns design scripts on for one project. Opening a project, or
reading a note, does not turn them on. Part scripts, the ones that assign
``part``, use the same confined process so an assistant cannot skip it.
A link that points outside the project is not written. A file with more
than one name is not used for the run, because writing it would change
every name. A script result that is a link is not read. Geometry and
pictures named by that result are opened only when they stay inside the
project. The check report is written by the process that ran the calls,
so the script does not supply it. The script log is not read when it has
another name.
"""

from __future__ import annotations

import builtins
import ctypes
import errno
import json
import os
import platform
import re
import select
import shutil
import signal
import stat
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

# Windows starts the child through this snippet. Loading the runner by name
# imports aicad before main() runs, so an import error never reaches the
# child's handler. A partial native import can then hang until the wall-clock
# limit. The runner file is an argument, so the project folder is not where
# that import starts.
_WINDOWS_CHILD_BOOT = r"""
import importlib.util, json, os, sys
from pathlib import Path
job = Path(sys.argv[1])
runner = Path(sys.argv[2])
try:
    spec = importlib.util.spec_from_file_location("aicad.sandbox_run", runner)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {runner}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    main = module.main
except Exception as exc:
    detail = f"{type(exc).__name__}: {exc}"
    try:
        (job / "result.json").write_text(
            json.dumps({"ok": False, "error": detail}) + "\n",
            encoding="utf-8",
        )
    except Exception:
        print(detail, file=sys.stderr)
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
_FS_MAKE_CHAR = 1 << 6
_FS_MAKE_DIR = 1 << 7
_FS_MAKE_REG = 1 << 8
_FS_MAKE_SOCK = 1 << 9
_FS_MAKE_FIFO = 1 << 10
_FS_MAKE_BLOCK = 1 << 11
_FS_MAKE_SYM = 1 << 12
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
    | _FS_MAKE_SOCK
    | _FS_MAKE_FIFO
    | _FS_MAKE_SYM
    | _FS_REFER
    | _FS_TRUNCATE
)
# Device nodes are handled and never granted. Symlinks, pipes, and socket
# nodes are granted only on the project folder, below.
_HANDLED_FS = _WORK_ACCESS | _FS_EXECUTE | _FS_MAKE_CHAR | _FS_MAKE_BLOCK

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
# socket call this filter would not see. ptrace, cross-process memory,
# pidfd_getfd, and a signal sent through a pidfd reach a process outside the script.
_ISOLATION_SYSCALLS = {
    "x86_64": (
        101,  # ptrace
        310,  # process_vm_readv
        311,  # process_vm_writev
        425,  # io_uring_setup
        426,  # io_uring_enter
        427,  # io_uring_register
        424,  # pidfd_send_signal
        438,  # pidfd_getfd
    ),
    "aarch64": (
        117,  # ptrace
        270,  # process_vm_readv
        271,  # process_vm_writev
        425,  # io_uring_setup
        426,  # io_uring_enter
        427,  # io_uring_register
        424,  # pidfd_send_signal
        438,  # pidfd_getfd
    ),
}
# A signal call is allowed only when its first argument is this process.
# tkill names a thread. The others name a thread group. A pidfd does not
# carry a pid this filter can read, so pidfd_send_signal is refused above.
_SIGNAL_SYSCALLS = {
    "x86_64": {"kill": 62, "tkill": 200, "tgkill": 234, "rt_sigqueueinfo": 129, "rt_tgsigqueueinfo": 297},
    "aarch64": {"kill": 129, "tkill": 130, "tgkill": 131, "rt_sigqueueinfo": 138, "rt_tgsigqueueinfo": 240},
}
_GETTID = {"x86_64": 186, "aarch64": 178}
# Install the filter on every thread that already exists, not only the caller.
_SECCOMP_FILTER_FLAG_TSYNC = 1
_THREAD_CONFINE_ERROR = "Could not confine every thread, so the script was not run."
# Calls that create or replace a file. Used only when a thread already exists
# and the script is about to be refused.
_NEW_FILE_SYSCALLS = {
    "x86_64": (
        2, 85, 257, 437, 83, 258, 87, 263, 82, 264, 316, 86, 265, 88, 266, 133, 259, 76, 77, 285, 40, 275, 326,
    ),
    "aarch64": (56, 437, 34, 35, 38, 276, 37, 36, 33, 46, 47, 71, 76, 285),
}
# Writes. Allowed only for the standard streams and the result the parent already opened.
_WRITE_SYSCALLS = {
    "x86_64": (1, 18, 20, 296, 328),
    "aarch64": (64, 68, 66, 70, 287),
}
# A shared mapping of a file can change that file without write. Anonymous memory stays allowed.
_MMAP = {"x86_64": 9, "aarch64": 222}
_MAP_SHARED = 0x01
_MAP_ANONYMOUS = 0x20
# A resource limit names a process in its first argument. Pid 0 is this process.
_PRLIMIT64 = {"x86_64": 302, "aarch64": 261}
# Owner, signal, and lease commands. The command is the second argument.
_FCNTL = {"x86_64": 72, "aarch64": 25}
_FCNTL_SIGNAL_COMMANDS = (8, 9, 10, 11, 15, 16, 1024, 1025, 1026)
# Terminal and socket notices that deliver a signal. The request is the second argument.
_IOCTL = {"x86_64": 16, "aarch64": 29}
_IOCTL_SIGNAL_COMMANDS = (0x40045436, 0x8901, 0x8902, 0x5410, 0x5414)
# seccomp_data.arch. A 32-bit int 0x80 call on x86_64 is a different architecture
# and a different syscall number, so a filter that only compares the native
# number would let it through. x32 numbers set bit 30 on this same architecture.
_AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
_X32_SYSCALL_BIT = 0x40000000
# How often the macOS parent checks the script's resident memory.
_MEMORY_POLL_SECONDS = 0.25
# Stop-the-world passes after a macOS script exits. Each pass freezes the
# processes it can see and looks again. There is no delay between passes.
_REAP_PASSES = 48
# How long to wait, once, for a killed descendant to exit.
_REAP_EXIT_SECONDS = 2
_LANDLOCK_SCOPE_SIGNAL = 1 << 1
# What this process actually installed. Tests read these after the sandbox
# starts. Writing them does not change the kernel ruleset.
_landlock_abi_used: int | None = None
_landlock_signal_scope = False
# Threads in this script only. RLIMIT_NPROC counts every thread of the user,
# so a fixed 256 was stopping geometry on a desktop that already had a browser open.
_PROCESS_CAP = 256
_PR_SET_CHILD_SUBREAPER = 36
_PROCESS_LIMIT = "The script was stopped because it started too many processes."
_CGROUP_MOVE_ERROR = "The process limit could not be applied, so the script was not run."

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


def file_bytes() -> int:
    """File-size cap for one script. An environment value cannot raise it."""
    return _capped("AICAD_SCRIPT_FILE_BYTES", FILE_BYTES, FILE_BYTES)


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
    """Append one design-script row. A log path that leaves the project is skipped.

    A symlink, junction, or file with another name is left as it is. Reading
    it would copy that other file into the project.
    """
    from aicad.paths import is_reparse, output_path, publish_text, shared_file

    root = Path(workspace).resolve()
    try:
        path = output_path(root, script_log_path(root), "Script log", ScriptError)
    except ScriptError:
        return
    if is_reparse(path) or shared_file(path):
        return
    row = {"time": _now(), "kind": "design", "ok": ok, "error": error, "script": script}
    line = json.dumps(row) + "\n"
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return
    try:
        publish_text(path, existing + line, root=root)
    except OSError:
        return


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


def _refuse_project_link(path: Path, root: Path, read_roots: list[Path], writing: bool) -> None:
    """Block a path outside the project, and a hard link that has another name."""
    inside = _inside(path, root)
    if inside and _shared_file(path):
        action = "write" if writing else "read"
        raise ScriptError(
            f"The script tried to {action} {path}, which has another name outside this use. "
            "A design script can only use files that belong to the project alone."
        )
    if not inside and (writing or not _under(path, read_roots)):
        action = "write" if writing else "read"
        raise ScriptError(
            f"The script tried to {action} {path}, which is outside the project folder {root}. "
            "A design script can only use files inside that folder."
        )


def _shared_file(path: Path) -> bool:
    from aicad.paths import shared_file

    return shared_file(path)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _refuse_multilink(workspace: Path) -> None:
    """Refuse a run when a file in the project has another name.

    Writing that file would change every name, including one outside the
    project. Directory link counts are not file names, and directory links
    are not followed.
    """
    from aicad.paths import is_reparse, shared_file

    root = workspace.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not is_reparse(current / name)]
        for name in filenames:
            path = current / name
            if is_reparse(path) or not shared_file(path):
                continue
            relative = path.relative_to(root).as_posix()
            raise ScriptError(
                f"{relative} is linked from more than one name, so the script was not run."
            )


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

    def py_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            writing = any(flag in str(mode) for flag in ("w", "a", "x", "+"))
            path = Path(os.fsdecode(file))
            if not path.is_absolute():
                path = Path.cwd() / path
            _refuse_project_link(path, root, read_roots, writing)
        return real_open(file, mode, *args, **kwargs)

    def os_open(path, flags, mode=0o777, *, dir_fd=None):
        if not isinstance(path, int):
            writing = bool(flags & _WRITE_FLAGS)
            file_path = Path(os.fsdecode(path))
            if not file_path.is_absolute():
                file_path = Path.cwd() / file_path
            _refuse_project_link(file_path, root, read_roots, writing)
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


# A stop that cannot remove its cgroup keeps the path and tries again later.
_leftover_cgroups: set[str] = set()
_leftover_cgroup_lock = threading.Lock()


def _prepare_pids_cgroup() -> Path | None:
    """A sibling cgroup with ``pids.max``, or None when that needs root.

    The limit has to be a sibling. Setting it on this process's own cgroup
    would cap the whole program, and a child of a cgroup that still holds
    processes cannot take the pids controller.
    """
    _retry_leftover_cgroups()
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


def _proc_cgroup_path(pid: int) -> Path | None:
    """This process's cgroup v2 directory, from ``/proc``, or None."""
    try:
        text = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8")
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


def _same_directory(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _move_into_pids_cgroup(cgroup: Path, pid: int) -> bool:
    """Move ``pid`` into ``cgroup``. False when the move did not stick.

    Writing the pid is not enough. The process has to show up in that cgroup
    afterwards, or ``pids.max`` does not apply to it.
    """
    try:
        (cgroup / "cgroup.procs").write_text(f"{pid}\n", encoding="utf-8")
    except OSError:
        return False
    current = _proc_cgroup_path(pid)
    if current is None:
        return False
    return _same_directory(current, cgroup)


def _require_pids_cgroup(cgroup: Path, pid: int) -> None:
    """Move ``pid`` into ``cgroup``, or refuse to run the script."""
    if not _move_into_pids_cgroup(cgroup, pid):
        raise ScriptError(_CGROUP_MOVE_ERROR)


def _join_pids_cgroup() -> None:
    """Move this process into the cgroup the parent prepared, before Landlock.

    A failed move is not ignored. The script does not run without the cap.
    """
    raw = os.environ.get("AICAD_PIDS_CGROUP")
    if not raw:
        return
    _require_pids_cgroup(Path(raw), os.getpid())


def _release_pids_cgroup(path: Path | None) -> None:
    """Remove a script cgroup. A directory that stays busy is tried again later."""
    _retry_leftover_cgroups()
    if path is None:
        return
    if _remove_pids_cgroup(path):
        with _leftover_cgroup_lock:
            _leftover_cgroups.discard(str(path))
        return
    with _leftover_cgroup_lock:
        _leftover_cgroups.add(str(path))


def _retry_leftover_cgroups() -> None:
    with _leftover_cgroup_lock:
        pending = list(_leftover_cgroups)
    for raw in pending:
        if _remove_pids_cgroup(Path(raw), attempts=1):
            with _leftover_cgroup_lock:
                _leftover_cgroups.discard(raw)


def _remove_pids_cgroup(path: Path, attempts: int = 25) -> bool:
    """Empty ``path`` and remove it. False when it is still there."""
    procs = path / "cgroup.procs"
    parent_procs = path.parent / "cgroup.procs"
    for _ in range(attempts):
        try:
            text = procs.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.split():
            try:
                pid = int(line)
            except ValueError:
                continue
            _kill_pid(pid)
            if parent_procs.is_file():
                try:
                    parent_procs.write_text(f"{pid}\n", encoding="utf-8")
                except OSError:
                    pass
        try:
            path.rmdir()
            return True
        except OSError:
            if not path.exists():
                return True
            if attempts > 1:
                time.sleep(0.04)
    return not path.exists()


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
        file_cap = file_bytes()
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_cap, file_cap))
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
    global _landlock_abi_used, _landlock_signal_scope
    abi = int(libc.syscall(create, None, 0, 1))
    # One choice for the struct and the record.
    use_signal_scope = abi >= 6
    if use_signal_scope:
        attr = _RulesetAttrScoped(_HANDLED_FS, net, _LANDLOCK_SCOPE_SIGNAL)
    else:
        attr = _RulesetAttr(_HANDLED_FS, net)
    _landlock_abi_used = abi
    _landlock_signal_scope = use_signal_scope
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


def _deny_commands(
    program: list,
    number: int,
    commands: tuple[int, ...],
    *,
    bpf_jeq: int,
    bpf_ld: int,
    bpf_ret: int,
    errno_eperm: int,
    allow: int,
) -> None:
    """Refuse ``number`` when its second argument is one of ``commands``.

    A nonzero high half is refused too. Any other command of this call is allowed.
    """
    count = len(commands)
    program.append(_SockFilter(bpf_jeq, 0, 6 + count, number))
    program.append(_SockFilter(bpf_ld, 0, 0, 28))
    program.append(_SockFilter(bpf_jeq, 1, 0, 0))
    program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
    program.append(_SockFilter(bpf_ld, 0, 0, 24))
    for index, command in enumerate(commands):
        program.append(_SockFilter(bpf_jeq, count - index, 0, command))
    program.append(_SockFilter(bpf_ret, 0, 0, allow))
    program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))


def _allow_only_this_process(
    program: list,
    number: int,
    pid: int,
    *,
    bpf_jeq: int,
    bpf_ld: int,
    bpf_ret: int,
    errno_eperm: int,
    allow: int,
) -> None:
    """Allow ``number`` only for pid 0 or ``pid``. Both mean this process."""
    program.append(_SockFilter(bpf_jeq, 0, 8, number))
    program.append(_SockFilter(bpf_ld, 0, 0, 20))
    program.append(_SockFilter(bpf_jeq, 1, 0, 0))
    program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
    program.append(_SockFilter(bpf_ld, 0, 0, 16))
    program.append(_SockFilter(bpf_jeq, 2, 0, 0))
    program.append(_SockFilter(bpf_jeq, 1, 0, pid))
    program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
    program.append(_SockFilter(bpf_ret, 0, 0, allow))


def _own_process_ids(arch: str) -> tuple[int, int]:
    """The process and thread this filter may signal. Anything else is refused."""
    pid = os.getpid()
    tid = int(_libc().syscall(_GETTID[arch]))
    if pid <= 0 or tid <= 0 or pid > 0xFFFFFFFF or tid > 0xFFFFFFFF:
        raise ScriptError("Could not apply the script's process limits, so the script was not run.")
    return pid, tid


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
    pid, tid = _own_process_ids(arch)
    for name, number in _SIGNAL_SYSCALLS[arch].items():
        # Not this call: skip the seven instructions that judge its target.
        program.append(_SockFilter(bpf_jeq, 0, 7, number))
        program.append(_SockFilter(bpf_ld, 0, 0, 20))
        program.append(_SockFilter(bpf_jeq, 1, 0, 0))
        program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
        program.append(_SockFilter(bpf_ld, 0, 0, 16))
        program.append(_SockFilter(bpf_jeq, 1, 0, tid if name == "tkill" else pid))
        program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
        program.append(_SockFilter(bpf_ret, 0, 0, allow))
    _allow_only_this_process(
        program,
        _PRLIMIT64[arch],
        pid,
        bpf_jeq=bpf_jeq,
        bpf_ld=bpf_ld,
        bpf_ret=bpf_ret,
        errno_eperm=errno_eperm,
        allow=allow,
    )
    _deny_commands(
        program,
        _FCNTL[arch],
        _FCNTL_SIGNAL_COMMANDS,
        bpf_jeq=bpf_jeq,
        bpf_ld=bpf_ld,
        bpf_ret=bpf_ret,
        errno_eperm=errno_eperm,
        allow=allow,
    )
    _deny_commands(
        program,
        _IOCTL[arch],
        _IOCTL_SIGNAL_COMMANDS,
        bpf_jeq=bpf_jeq,
        bpf_ld=bpf_ld,
        bpf_ret=bpf_ret,
        errno_eperm=errno_eperm,
        allow=allow,
    )
    program.append(_SockFilter(bpf_ret, 0, 0, allow))
    _install_filter(program)


def _install_filter(program: list) -> None:
    """Install ``program`` on this thread and on every thread that already exists."""
    arch = _arch()
    if arch is None:
        raise ScriptError(sandbox_supported()[1])
    filters = (_SockFilter * len(program))(*program)
    prog = _SockFprog(len(program), filters)
    if _libc().syscall(_SECCOMP[arch], 1, _SECCOMP_FILTER_FLAG_TSYNC, ctypes.byref(prog)) != 0:
        raise ScriptError(
            "Could not apply the script's process limits, so the script was not run."
        )


def _python_thread_exists() -> bool:
    """True when Python already started a thread besides this one.

    A geometry library can start its own threads. Those are created after
    confinement, so they inherit it. A thread that is already running does not.
    """
    import threading

    return threading.active_count() > 1


def _block_new_files(result_fd: int | None) -> None:
    """Refuse new files, and writes except the standard streams and ``result_fd``.

    Called only after the process filter is in place and only when another
    thread already exists. The script is not run on that path.
    """
    arch = _arch()
    if arch is None:
        raise ScriptError(sandbox_supported()[1])
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
    for number in _NEW_FILE_SYSCALLS[arch]:
        program.append(_SockFilter(bpf_jeq, 0, 1, number))
        program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
    allowed = [1, 2]
    if result_fd is not None and result_fd not in allowed and 0 <= result_fd <= 0xFFFFFFFF:
        allowed.append(result_fd)
    count = len(allowed)
    for number in _WRITE_SYSCALLS[arch]:
        program.append(_SockFilter(bpf_jeq, 0, 6 + count, number))
        program.append(_SockFilter(bpf_ld, 0, 0, 20))
        program.append(_SockFilter(bpf_jeq, 1, 0, 0))
        program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
        program.append(_SockFilter(bpf_ld, 0, 0, 16))
        for index, descriptor in enumerate(allowed):
            program.append(_SockFilter(bpf_jeq, count - index, 0, descriptor))
        program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
        program.append(_SockFilter(bpf_ret, 0, 0, allow))
    # Shared file mappings can change a file. Anonymous memory stays available.
    program.append(_SockFilter(bpf_jeq, 0, 8, _MMAP[arch]))
    program.append(_SockFilter(bpf_ld, 0, 0, 44))
    program.append(_SockFilter(bpf_jeq, 1, 0, 0))
    program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
    program.append(_SockFilter(bpf_ld, 0, 0, 40))
    program.append(_SockFilter(bpf_jset, 1, 0, _MAP_ANONYMOUS))
    program.append(_SockFilter(bpf_jset, 1, 0, _MAP_SHARED))
    program.append(_SockFilter(bpf_ret, 0, 0, allow))
    program.append(_SockFilter(bpf_ret, 0, 0, errno_eperm))
    program.append(_SockFilter(bpf_ret, 0, 0, allow))
    _install_filter(program)


# A literal public address. Loopback is often allowed inside a container, and a
# name would make a DNS failure look like a closed network.
_WINDOWS_TCP_PROBE = ("1.1.1.1", 443)
_WINDOWS_UDP_PROBE = ("1.1.1.1", 53)
# internetClient, internetClientServer, privateNetworkClientServer.
_NETWORK_CAPABILITY_SIDS = frozenset({"S-1-15-3-1", "S-1-15-3-2", "S-1-15-3-3"})


def confined_errno(error: OSError) -> bool:
    """True when this error is the operating system refusing the sandbox.

    A timeout, a DNS failure, and any other code are not a refusal. A packet
    that was allowed to wait is not proof the network is off.
    """

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


def _error_codes(error: BaseException) -> str:
    winerror = getattr(error, "winerror", None)
    number = getattr(error, "errno", None)
    return f"winerror={winerror} errno={number}"


def _network_timeout(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    if getattr(error, "winerror", None) == 10060:
        return True
    return getattr(error, "errno", None) in {10060, 110}


def _network_dns(error: BaseException) -> bool:
    import socket

    if isinstance(error, socket.gaierror):
        return True
    number = getattr(error, "winerror", None)
    errno = getattr(error, "errno", None)
    return number in {11001, 11002, 11003, 11004} or errno in {11001, 11002, 11003, 11004}


def network_attempt_label(error: BaseException | None, *, success: str) -> str:
    """What one connect or send did. Only a sandbox refusal is a denial."""

    if error is None:
        return success
    if _network_timeout(error):
        return f"timeout {_error_codes(error)}"
    if _network_dns(error):
        return f"dns {_error_codes(error)}"
    if isinstance(error, OSError) and confined_errno(error):
        return f"denied {_error_codes(error)}"
    return f"error {_error_codes(error)}"


def _public_probe_address(address: str) -> bool:
    text = address.strip().lower()
    if text in {"localhost", "::1", "0.0.0.0"} or text.startswith("127."):
        return False
    return True


# Set by the parent when it is listening. Dotted IPv4 only, so this cannot
# turn into a name lookup. Each item is address, TCP port, UDP port.
_WINDOWS_RECEIVER_ENV = "AICAD_WIN_NET_RECEIVERS"
_WINDOWS_RECEIVER_ITEM = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5}):(\d{1,5})$")
_WINDOWS_PROBE_PAYLOAD = b"aicad-net-probe"
# The last Windows network detail, so the probe result can show it after a pass.
_windows_net_probe_text = ""


def _ipv4_text(address: str) -> bool:
    parts = address.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
            return False
        if int(part) > 255:
            return False
    return True


def windows_receiver_targets() -> tuple[tuple[str, int, int], ...]:
    """Local receivers the parent asked the network check to try.

    A name is ignored. The check must not look anything up.
    """

    found: list[tuple[str, int, int]] = []
    raw = os.environ.get(_WINDOWS_RECEIVER_ENV, "")
    for part in raw.split(";"):
        item = part.strip()
        if not item:
            continue
        match = _WINDOWS_RECEIVER_ITEM.fullmatch(item)
        if match is None:
            continue
        address, tcp_text, udp_text = match.group(1), match.group(2), match.group(3)
        tcp_port = int(tcp_text)
        udp_port = int(udp_text)
        if not _ipv4_text(address):
            continue
        if tcp_port <= 0 or tcp_port > 65535 or udp_port <= 0 or udp_port > 65535:
            continue
        found.append((address, tcp_port, udp_port))
    return tuple(found)


def _windows_one_attempt(kind: str, address: str, port: int) -> str:
    """One TCP connect or one UDP send.

    connect() on a UDP socket only selects the destination. The following
    send() hands the datagram to the stack. A successful send is not a
    refusal, connected or not.
    """

    import socket

    if kind == "tcp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        try:
            try:
                sock.connect((address, port))
            except OSError as exc:
                return network_attempt_label(exc, success="connected")
            return "connected"
        finally:
            sock.close()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    try:
        try:
            if kind == "udp-sendto":
                # An unbound sendto reports a blocked local port as WSAEINVAL.
                # That is not a sent datagram, and it is not the access error
                # the block returns for a bound socket. Bind first, then send.
                sock.bind(("0.0.0.0", 0))
                sock.sendto(_WINDOWS_PROBE_PAYLOAD, (address, port))
            else:
                sock.connect((address, port))
                sock.send(_WINDOWS_PROBE_PAYLOAD)
        except OSError as exc:
            return network_attempt_label(exc, success="sent")
        return "sent"
    finally:
        sock.close()


def windows_network_attempts() -> tuple[tuple[str, str], ...]:
    """TCP, an unconnected UDP send, and a connected UDP send.

    Receiver attempts run first, so a later timeout cannot skip them.
    Loopback is not a refusal. A public address that is loopback is recorded
    as loopback and does not count as off.
    """

    attempts: list[tuple[str, str]] = []
    for address, tcp_port, udp_port in windows_receiver_targets():
        attempts.append(
            (f"tcp@{address}:{tcp_port}", _windows_one_attempt("tcp", address, tcp_port))
        )
        attempts.append(
            (
                f"udp-sendto@{address}:{udp_port}",
                _windows_one_attempt("udp-sendto", address, udp_port),
            )
        )
        attempts.append(
            (
                f"udp-connected@{address}:{udp_port}",
                _windows_one_attempt("udp-connected", address, udp_port),
            )
        )
    tcp_host, tcp_port = _WINDOWS_TCP_PROBE
    udp_host, udp_port = _WINDOWS_UDP_PROBE
    if not _public_probe_address(tcp_host):
        attempts.append(("tcp", "loopback"))
    else:
        attempts.append(("tcp", _windows_one_attempt("tcp", tcp_host, tcp_port)))
    if not _public_probe_address(udp_host):
        attempts.append(("udp-sendto", "loopback"))
        attempts.append(("udp-connected", "loopback"))
    else:
        attempts.append(("udp-sendto", _windows_one_attempt("udp-sendto", udp_host, udp_port)))
        attempts.append(
            ("udp-connected", _windows_one_attempt("udp-connected", udp_host, udp_port))
        )
    return tuple(attempts)


def _capability_text(capabilities: tuple[str, ...] | None) -> str:
    if capabilities is None:
        return "unread"
    if not capabilities:
        return "none"
    return ",".join(capabilities)


def windows_network_detail(
    *,
    attempts: tuple[tuple[str, str], ...],
    package: str,
    capabilities: tuple[str, ...] | None,
) -> str:
    tcp_host, tcp_port = _WINDOWS_TCP_PROBE
    udp_host, udp_port = _WINDOWS_UDP_PROBE
    targets = [f"{tcp_host}:{tcp_port}", f"{udp_host}:{udp_port}"]
    for address, receiver_tcp, receiver_udp in windows_receiver_targets():
        targets.append(f"{address}:{receiver_tcp}:{receiver_udp}")
    shown = "; ".join(f"{name}={label}" for name, label in attempts)
    return (
        f"probe={','.join(targets)}; {shown}; "
        f"package={package}; capabilities={_capability_text(capabilities)}"
    )


def windows_network_is_off(
    *,
    attempts: tuple[tuple[str, str], ...],
    package: str,
    capabilities: tuple[str, ...] | None,
) -> bool:
    """True only when every send was refused and the token has no network capability.

    The public TCP connect, the unconnected UDP send, and the connected UDP
    send all have to be present. A successful send is not off, because the
    datagram may already have left. An unreadable capability list is not an
    empty one. The package id has to be present, because that is the
    container the block names.
    """

    labels = {name: label for name, label in attempts}
    if any(name not in labels for name in ("tcp", "udp-sendto", "udp-connected")):
        return False
    if capabilities is None or not package.upper().startswith("S-1-15-2-"):
        return False
    present = {item.upper() for item in capabilities}
    if present & {item.upper() for item in _NETWORK_CAPABILITY_SIDS}:
        return False
    return all(label.startswith("denied ") for _name, label in attempts)


def windows_network_failure_text(
    *,
    attempts: tuple[tuple[str, str], ...],
    package: str,
    capabilities: tuple[str, ...] | None,
) -> str:
    return (
        "Windows did not turn the network off, so the script was not run. "
        + windows_network_detail(attempts=attempts, package=package, capabilities=capabilities)
    )


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


def apply_sandbox(
    workspace: Path,
    network: bool,
    result_fd: int | None = None,
    *,
    extra_thread: bool = False,
    result_path: Path | None = None,
) -> list[Path]:
    """Confine the current process. Called only in the script's child process.

    ``result_fd`` is a result file this process already opened. It is used only
    when another thread already exists and the script is refused. ``extra_thread``
    is set when a thread existed before this module was imported.
    """
    from aicad.plugins import seal_sandbox_plugins
    from aicad.sandbox_state import mark_sandboxed

    mark_sandboxed()
    seal_sandbox_plugins()
    _refuse_foreign_kill()
    workspace = workspace.resolve()
    from aicad.paths import output_dir

    private = output_dir(workspace, workspace / ".aicad", "Project data", ScriptError)
    temporary = output_dir(workspace, private / "tmp", "Temporary files", ScriptError)
    os.environ["TMPDIR"] = str(temporary)
    os.environ["TMP"] = str(temporary)
    os.environ["TEMP"] = str(temporary)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    roots = _read_roots()
    if sys.platform == "win32":
        from aicad.sandbox_windows import identity_failures, windows_token_network

        failures = identity_failures(memory_bytes())
        if failures:
            raise ScriptError(
                "Windows did not confine the script ("
                + "; ".join(failures)
                + "), so the script was not run."
            )
        # The token and the job are already applied. This probe reads that token.
        if not network:
            global _windows_net_probe_text
            package, capabilities = windows_token_network()
            attempts = windows_network_attempts()
            _windows_net_probe_text = windows_network_detail(
                attempts=attempts, package=package, capabilities=capabilities
            )
            if not windows_network_is_off(
                attempts=attempts, package=package, capabilities=capabilities
            ):
                raise ScriptError(
                    "Windows did not turn the network off, so the script was not run. "
                    + _windows_net_probe_text
                )
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
    # The address cap is applied after the geometry libraries are mapped.
    # Mapping them under the cap fails before the script can run.
    _landlock(workspace, network=network, read_roots=roots)
    extra = extra_thread or _python_thread_exists()
    _block_sockets(network=network)
    if extra or _python_thread_exists():
        fd = result_fd
        opened = False
        if fd is None and result_path is not None:
            fd = os.open(
                result_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            opened = True
        _block_new_files(fd)
        if opened and fd is not None:
            os.write(
                fd,
                (json.dumps({"ok": False, "error": _THREAD_CONFINE_ERROR}) + "\n").encode(),
            )
            os.close(fd)
        raise ScriptError(_THREAD_CONFINE_ERROR)
    return roots


# Imported while the interpreter is starting, before the sandbox is applied.
# ``aicad`` is this program. The other names are imported by site startup
# (Debian and Ubuntu also import ``apport_python_hook`` from sitecustomize).
_STARTUP_MODULE_NAMES = frozenset(
    {
        "aicad",
        "apport_python_hook",
        "sitecustomize",
        "usercustomize",
    }
)


def _under_directory(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _pythonpath_for_child(workspace: Path, raw: str | None) -> str | None:
    """Drop import entries that would load Python from the project.

    The child uses the project as its working directory. A relative entry
    would be read from that folder, so every entry that is kept is absolute
    and taken from this process's current directory. The project itself, any
    directory inside it, and a parent directory that would import the project
    under a name Python loads at startup are removed.
    """
    if not raw:
        return None
    root = workspace.resolve()
    kept: list[str] = []
    for entry in raw.split(os.pathsep):
        text = entry.strip()
        if text in ("", ".", ".."):
            continue
        try:
            candidate = Path(text).expanduser()
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            candidate = candidate.resolve()
        except OSError:
            continue
        if _under_directory(candidate, root):
            continue
        if root.name in _STARTUP_MODULE_NAMES:
            try:
                if (candidate / root.name).resolve() == root:
                    continue
            except OSError:
                continue
        kept.append(str(candidate))
    if not kept:
        return None
    return os.pathsep.join(kept)


# A parent can be started with a loader or interpreter variable. Those must
# not reach a script or another program this process starts.
_DROPPED_ENV = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "GCONV_PATH",
        "GIT_EXTERNAL_DIFF",
        "GLIBC_TUNABLES",
        "HOSTALIASES",
        "LIBPATH",
        "LOCALDOMAIN",
        "LOCPATH",
        "NLSPATH",
        "RES_OPTIONS",
        "GIT_PAGER",
        "GIT_SSH_COMMAND",
        "NODE_OPTIONS",
        "NODE_PATH",
        "PERL5LIB",
        "PERL5OPT",
        "PS4",
        "PYTHONEXECUTABLE",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTHONWARNINGS",
        "RUBYLIB",
        "RUBYOPT",
        "SHELLOPTS",
    }
)


def _system_path() -> str:
    """Directories the operating system uses, not the parent's PATH."""

    parts = [str(Path(sys.executable).resolve().parent)]
    if os.name == "nt":
        root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
        parts.append(str(Path(root) / "System32"))
        parts.append(root)
    else:
        parts.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    kept: list[str] = []
    for part in parts:
        if part and part not in kept:
            kept.append(part)
    return os.pathsep.join(kept)


def _trusted_pythonpath(workspace: Path | None) -> str:
    """Import roots for a child: this install, not the parent's PYTHONPATH."""

    install = Path(__file__).resolve().parent.parent
    prefixes: list[Path] = [install]
    for item in (sys.prefix, getattr(sys, "base_prefix", sys.prefix)):
        try:
            prefixes.append(Path(item).resolve())
        except OSError:
            continue
    workspace_resolved = None
    if workspace is not None:
        try:
            workspace_resolved = workspace.resolve()
        except OSError:
            workspace_resolved = None
    roots: list[str] = []
    candidates = [install]
    for entry in sys.path:
        if not entry:
            continue
        try:
            candidates.append(Path(entry).resolve())
        except OSError:
            continue
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if workspace_resolved is not None and (
            resolved == workspace_resolved or _under_directory(resolved, workspace_resolved)
        ):
            continue
        trusted = resolved == install or any(
            resolved == prefix or prefix in resolved.parents for prefix in prefixes
        )
        if not trusted:
            continue
        text = str(resolved)
        if text not in roots:
            roots.append(text)
    return os.pathsep.join(roots)


def scrubbed_environment(workspace: Path | None = None) -> dict[str, str]:
    """An environment for a child. Loader and startup variables are omitted."""

    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith("LD_") or upper.startswith("DYLD_") or upper == "PATH":
            continue
        if upper in _DROPPED_ENV:
            continue
        if any(part in upper for part in _SECRET_PARTS):
            continue
        if key in ("AICAD_CONFIG", "AICAD_SETTINGS", "AICAD_PLUGIN_CONSENT", "XDG_CONFIG_HOME"):
            continue
        env[key] = value
    env["PATH"] = _system_path()
    env["PYTHONSAFEPATH"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    pythonpath = _trusted_pythonpath(workspace)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    _restore_os_environment(env)
    return env


# Windows refuses to start a program when these are missing. A scrub keeps
# them even when a drop rule would have removed the name.
_OS_ENV = ("COMSPEC", "PATHEXT", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "WINDIR")


def _restore_os_environment(env: dict[str, str]) -> None:
    """Put back the variables Windows needs to start a process.

    Windows stores these names in uppercase. A mixed-case copy is the same
    variable, so the child keeps the canonical name.
    """

    parent = {key.upper(): value for key, value in os.environ.items()}
    carried = {key.upper(): value for key, value in env.items()}
    for key in [key for key in env if key.upper() in _OS_ENV and key not in _OS_ENV]:
        del env[key]
    for name in _OS_ENV:
        if env.get(name):
            continue
        value = carried.get(name) or parent.get(name)
        if value:
            env[name] = value


def program_environment() -> dict[str, str]:
    """Environment for CalculiX, ffmpeg, a slicer, and other helper programs."""

    env = scrubbed_environment(None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONSTARTUP", None)
    return env


def _child_env(
    workspace: Path,
    job: Path,
    network: bool,
    tmp: Path | None = None,
    cache: Path | None = None,
) -> dict[str, str]:
    env = scrubbed_environment(workspace)
    # Do not prepend the working directory to sys.path. The working directory
    # is the project, and a module there would run before the sandbox.
    env["PYTHONSAFEPATH"] = "1"
    env["AICAD_SANDBOX_CHILD"] = "1"
    env["AICAD_WORKSPACE"] = str(workspace)
    env["AICAD_JOB"] = str(job)
    env["AICAD_NETWORK"] = "1" if network else "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    tmp_path = tmp if tmp is not None else workspace / ".aicad" / "tmp"
    tmp_text = str(tmp_path)
    env["TMPDIR"] = tmp_text
    env["TMP"] = tmp_text
    env["TEMP"] = tmp_text
    if sys.platform == "win32":
        # ezdxf and matplotlib otherwise stat the real user profile. An
        # AppContainer is denied that path. Do not redirect USERPROFILE:
        # that made every script fail with STATUS_INVALID_HANDLE.
        # The font list is built once in the Python install. The other
        # caches stay in the project, which the container can write.
        cache_path = cache if cache is not None else workspace / ".aicad" / "cache"
        cache_text = str(cache_path)
        font_home = _windows_font_cache_home()
        font_file = font_home / "ezdxf" / "font_manager_cache.json"
        if font_file.is_file() and font_file.stat().st_size > 0:
            env["XDG_CACHE_HOME"] = str(font_home)
        else:
            env["XDG_CACHE_HOME"] = cache_text
        env["XDG_CONFIG_HOME"] = cache_text
        env["XDG_DATA_HOME"] = cache_text
        env["MPLCONFIGDIR"] = cache_text
    return env


def _windows_font_cache_home() -> Path:
    """A font list the parent builds once, so each script does not scan fonts."""

    root = Path(sys.prefix) / "aicad-font-cache"
    target = root / "ezdxf" / "font_manager_cache.json"
    root.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 0:
        return root
    try:
        from aicad.paths import prepare_geometry_import

        prepare_geometry_import()
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
    if pid <= 1 or pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _related_pids(
    root_ids: set[int],
    identities: dict[int, tuple[int, int]],
    holders: set[int],
) -> set[int]:
    """Pids holding the sentinel, plus anyone chained to ``root_ids``.

    ``identities`` maps a pid to ``(unique_id, parent_unique_id)``. The parent
    id stays put when a process leaves the group or is reparented.
    """
    known = {int(item) for item in root_ids if isinstance(item, int) and item > 0}
    found = {int(pid) for pid in holders if isinstance(pid, int) and pid > 1 and pid != os.getpid()}
    changed = True
    while changed:
        changed = False
        for pid, (unique_id, parent_id) in identities.items():
            if not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
                continue
            if parent_id not in known and pid not in found:
                continue
            if isinstance(unique_id, int) and unique_id > 0 and unique_id not in known:
                known.add(unique_id)
                changed = True
            if pid not in found:
                found.add(pid)
                changed = True
    return found


def _refuse_unaccounted(ok: bool, survivors: set[int]) -> None:
    """A run whose processes could not be listed is not a clean result."""
    if ok and not survivors:
        return
    raise ScriptError(
        "The sandbox could not account for every process the script started, so the run was stopped."
    )


class _Account:
    """Whether macOS process listings for one script stayed usable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ok = True
        self.ids: set[int] = set()

    def fail(self) -> None:
        with self._lock:
            self.ok = False

    def add_ids(self, ids: set[int]) -> None:
        with self._lock:
            self.ids.update(item for item in ids if isinstance(item, int) and item > 0)

    def snapshot(self) -> tuple[bool, set[int]]:
        with self._lock:
            return self.ok, set(self.ids)


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
                timeout=2,
                env=program_environment(),
            )
        except (OSError, subprocess.TimeoutExpired):
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


def _ps_descendants(leader: int) -> set[int] | None:
    """Children listed by ``ps``. None when ``ps`` cannot be read.

    This is not enough to see a child that has already left the group. Callers
    use it only to kill what it can see, and then refuse a clean result.
    """
    if leader <= 1:
        return set()
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
            env=program_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    parent_of: dict[int, int] = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            parent_of[int(parts[0])] = int(parts[1])
        except ValueError:
            continue
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


def _pid_exists(pid: int) -> bool:
    """True when ``pid`` is still a process. A zombie counts as gone."""
    from aicad.sandbox_macos import process_runtime

    if pid <= 1 or pid == os.getpid():
        return False
    runtime = process_runtime(pid)
    if runtime is not None and runtime[1]:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


def _walk_children(leader: int) -> tuple[set[int], dict[int, tuple[int, int]], bool]:
    """Descendants by parent link, without listing open files.

    The bool is False when a process was still alive and its children could
    not be listed. A dead process is an empty walk, not a failed one.
    """
    from aicad.sandbox_macos import child_pids, process_identity

    found: set[int] = set()
    identities: dict[int, tuple[int, int]] = {}
    ok = True
    if leader <= 1 or leader == os.getpid():
        return found, identities, ok
    stack = [leader]
    walked: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in walked:
            continue
        walked.add(pid)
        if len(walked) > 500:
            ok = False
            break
        direct = child_pids(pid)
        if direct is None:
            if _pid_exists(pid):
                ok = False
            continue
        for child in direct:
            if child <= 1 or child == os.getpid() or child == leader or child in found:
                continue
            found.add(child)
            identity = process_identity(child)
            if identity is not None and isinstance(identity[0], int) and identity[0] > 0:
                identities[child] = identity
            stack.append(child)
    return found, identities, ok


def _macos_quick(leader: int, sentinel: str, root_ids: set[int]) -> tuple[set[int], set[int], bool]:
    """Descendants to stop while the script is still running.

    This path uses the parent link only. ``sentinel`` is read by the final
    pass, so a child is not left running while an open-file listing catches up.
    """
    del sentinel
    found, identities, ok = _walk_children(leader)
    ids = {identity[0] for identity in identities.values()}
    ids.update(item for item in root_ids if isinstance(item, int) and item > 0)
    from aicad.sandbox_macos import process_identity

    leader_id = process_identity(leader)
    if leader_id is not None and isinstance(leader_id[0], int) and leader_id[0] > 0:
        ids.add(leader_id[0])
    return found, ids, ok


def _macos_full(leader: int, sentinel: str, root_ids: set[int]) -> tuple[set[int], dict[int, tuple[int, int]], bool]:
    """Every pid still tied to the script, after it has been allowed to leave the group."""
    from aicad.sandbox_macos import all_pids, pids_holding_path, process_identity

    ok = True
    holders = pids_holding_path(sentinel)
    if holders is None:
        ok = False
        ps_found = _ps_descendants(leader)
        holders = [] if ps_found is None else list(ps_found)
    identities: dict[int, tuple[int, int]] = {}
    table = all_pids()
    if table is None:
        ok = False
        table = []
    for pid in table:
        if pid <= 1 or pid == os.getpid():
            continue
        identity = process_identity(pid)
        if identity is not None:
            identities[pid] = identity
    found = _related_pids(root_ids, identities, set(holders))
    leader_id = process_identity(leader)
    if leader_id is not None:
        found.update(_related_pids({leader_id[0]}, identities, found))
    try:
        walked, walked_ids, walk_ok = _walk_children(leader)
    except OSError:
        walked, walked_ids, walk_ok = set(), {}, False
    if not walk_ok:
        ok = False
    found.update(walked)
    for pid, identity in walked_ids.items():
        identities.setdefault(pid, identity)
    return found, identities, ok


def _outside_group(pid: int, leader: int) -> bool | None:
    """True when ``pid`` is not in the script group.

    None means the process is already gone. A group id that cannot be read
    while the process is alive counts as outside the group.
    """
    from aicad.sandbox_macos import process_runtime

    runtime = process_runtime(pid)
    if runtime is not None:
        pgid, zombie = runtime
        if zombie:
            return None
        return pgid != leader
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return None
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return None
        return True
    return pgid != leader


def _recycled(pid: int, identities: dict[int, tuple[int, int]]) -> bool:
    """True when ``pid`` is no longer the process that was listed."""
    from aicad.sandbox_macos import process_identity

    previous = identities.get(pid)
    if previous is None:
        return False
    current = process_identity(pid)
    if current is None:
        return False
    return current[0] != previous[0]


def _sweep_escaped(
    leader: int,
    sentinel: str,
    account: _Account,
    seen: set[int],
    lock: threading.Lock,
) -> set[int]:
    """Stop processes that have left the group. The waiter calls this itself.

    The parent walk is enough to see them while the script is alive. ``sentinel``
    is read by the final pass, not while the script is still running.
    """
    del sentinel
    _reliable, root_ids = account.snapshot()
    try:
        found, identities, listed = _walk_children(leader)
    except OSError:
        account.fail()
        return set()
    ids = {identity[0] for identity in identities.values()}
    ids.update(item for item in root_ids if isinstance(item, int) and item > 0)
    if not listed:
        account.fail()
    else:
        account.add_ids(ids)
    escaped: list[int] = []
    for pid in found:
        if pid == leader or pid == os.getpid() or _recycled(pid, identities):
            continue
        if _outside_group(pid, leader) is True:
            escaped.append(pid)
    with lock:
        seen.update(found)
    for pid in escaped:
        if _recycled(pid, identities):
            continue
        _signal_pid(pid, signal.SIGSTOP)
        _signal_led_group(pid, signal.SIGSTOP)
        _signal_led_group(pid, signal.SIGKILL)
        _signal_pid(pid, signal.SIGKILL)
    return found


def _wait_script(proc: subprocess.Popen, wall: float, sweep) -> tuple[bytes, bytes]:
    """Wait for ``proc``. On macOS, ``sweep`` runs while the script is still going."""
    if sweep is None:
        return proc.communicate(timeout=wall)
    deadline = time.monotonic() + wall
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, wall)
        try:
            return proc.communicate(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            sweep()


def _exited(pid: int, expected: tuple[int, int] | None) -> bool:
    """True when ``pid`` is gone, a zombie, or a different process now."""
    from aicad.sandbox_macos import process_identity, process_runtime

    if pid <= 1 or pid == os.getpid():
        return True
    runtime = process_runtime(pid)
    if runtime is not None and runtime[1]:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return True
        return False
    if expected is None:
        return False
    identity = process_identity(pid)
    if identity is None:
        return False
    return identity[0] != expected[0]


def _still_alive(pids: set[int], identities: dict[int, tuple[int, int]]) -> set[int]:
    """Pids that are still running. A recycled pid or a zombie is not alive."""
    return {pid for pid in pids if not _exited(pid, identities.get(pid))}


def _signal_pid(pid: int, sig: int) -> None:
    if pid <= 1 or pid == os.getpid():
        return
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def _signal_group(leader: int, sig: int, root_ids: set[int] | None = None) -> None:
    """Signal the script group when ``leader`` is still that process.

    A pid that has already been reused is left alone. Descendants are
    signalled by pid after the same check.
    """
    if leader <= 1 or leader == os.getpid():
        return
    if not root_ids:
        return
    from aicad.sandbox_macos import process_identity

    identity = process_identity(leader)
    if identity is None or identity[0] not in root_ids:
        return
    try:
        os.killpg(leader, sig)
    except OSError:
        pass


def _signal_led_group(pid: int, sig: int) -> None:
    """Signal the group ``pid`` leads. A follower is left to its own signal."""
    from aicad.sandbox_macos import process_runtime

    runtime = process_runtime(pid)
    if runtime is None:
        return
    pgid, zombie = runtime
    if zombie or pgid != pid:
        return
    try:
        os.killpg(pid, sig)
    except OSError:
        pass


def _freeze_known(
    pids: set[int],
    root_ids: set[int],
    stopped: set[int],
    frozen_ids: dict[int, tuple[int, int]],
) -> None:
    """Stop processes already tied to the script, before a slower listing."""
    from aicad.sandbox_macos import process_identity

    if not root_ids:
        return
    for pid in pids:
        if pid <= 1 or pid == os.getpid() or pid in stopped or _exited(pid, None):
            continue
        identity = process_identity(pid)
        if identity is None:
            continue
        if identity[0] not in root_ids and identity[1] not in root_ids:
            continue
        frozen_ids.setdefault(pid, identity)
        _signal_pid(pid, signal.SIGSTOP)
        stopped.add(pid)


def _arm_exit_watch(pids: list[int]):
    """Register an exit watch. None when this platform cannot wait that way."""
    if sys.platform != "darwin" or not pids:
        return None
    kqueue = getattr(select, "kqueue", None)
    kevent = getattr(select, "kevent", None)
    filt = getattr(select, "KQ_FILTER_PROC", None)
    note = getattr(select, "KQ_NOTE_EXIT", None)
    add = getattr(select, "KQ_EV_ADD", 0)
    enable = getattr(select, "KQ_EV_ENABLE", 0)
    if kqueue is None or kevent is None or filt is None or note is None:
        return None
    try:
        queue = kqueue()
    except OSError:
        return None
    changes = []
    try:
        for pid in pids:
            changes.append(kevent(pid, filter=filt, flags=add | enable, fflags=note))
    except (OSError, OverflowError, ValueError):
        try:
            queue.close()
        except OSError:
            pass
        return None
    return queue, changes


def _take_exits(ready, remaining: set[int]) -> None:
    error_flag = getattr(select, "KQ_EV_ERROR", 0)
    note = getattr(select, "KQ_NOTE_EXIT", 0)
    for event in ready:
        ident = int(event.ident)
        if error_flag and int(event.flags) & error_flag:
            if int(event.data) == errno.ESRCH:
                remaining.discard(ident)
            continue
        if note and int(event.fflags) & note:
            remaining.discard(ident)


def _finish_exit_watch(queue, changes, remaining: set[int], identities: dict[int, tuple[int, int]]) -> set[int]:
    try:
        ready = queue.control(changes, len(changes) or 1, 0)
    except OSError:
        return _still_alive(remaining, identities)
    _take_exits(ready, remaining)
    if not remaining:
        return set()
    try:
        ready = queue.control(None, len(remaining), _REAP_EXIT_SECONDS)
    except OSError:
        return _still_alive(remaining, identities)
    _take_exits(ready, remaining)
    return _still_alive(remaining, identities)


def _kill_and_wait(
    pids: set[int],
    leader: int,
    root_ids: set[int] | None = None,
    identities: dict[int, tuple[int, int]] | None = None,
) -> set[int]:
    """Kill these pids and wait until they exit. Returns any that are still alive."""
    known = identities or {}
    targets = [
        pid
        for pid in pids
        if isinstance(pid, int) and pid > 1 and pid != os.getpid() and not _exited(pid, known.get(pid))
    ]
    armed = _arm_exit_watch(targets)
    for pid in targets:
        _signal_pid(pid, signal.SIGKILL)
    _signal_group(leader, signal.SIGKILL, root_ids)
    if armed is None:
        return _still_alive(set(targets), known)
    queue, changes = armed
    try:
        return _finish_exit_watch(queue, changes, set(targets), known)
    finally:
        try:
            queue.close()
        except OSError:
            pass


def _reap_macos(leader: int, sentinel: str, account: _Account, seen: set[int], lock: threading.Lock) -> None:
    """Stop every descendant, then wait until a second look is empty.

    A child created between the look and the stop shows up on the next look,
    because a stopped process cannot create one. The call does not return
    while any of those processes is still alive. A listing that fails is not
    a clean run.
    """
    ok, root_ids = account.snapshot()
    root_ids = set(root_ids)
    stopped: set[int] = set()
    frozen_ids: dict[int, tuple[int, int]] = {}
    seen_clean = False
    with lock:
        known = set(seen)
    _freeze_known(known, root_ids, stopped, frozen_ids)
    _signal_group(leader, signal.SIGSTOP, root_ids)
    for _pass in range(_REAP_PASSES):
        try:
            walked, walked_ids, walk_ok = _walk_children(leader)
        except OSError:
            walked, walked_ids, walk_ok = set(), {}, False
        fresh_ids = {identity[0] for identity in walked_ids.values()}
        if walk_ok:
            if fresh_ids:
                account.add_ids(fresh_ids)
                root_ids.update(fresh_ids)
        else:
            ok = False
        for pid, identity in walked_ids.items():
            frozen_ids.setdefault(pid, identity)
        new_walk = {
            pid
            for pid in walked
            if pid not in stopped and not _exited(pid, frozen_ids.get(pid) or walked_ids.get(pid))
        }
        if new_walk:
            for pid in new_walk:
                _signal_pid(pid, signal.SIGSTOP)
                stopped.add(pid)
            _signal_group(leader, signal.SIGSTOP, root_ids)
            seen_clean = False
            continue
        try:
            found, identities, listed = _macos_full(leader, sentinel, root_ids)
        except OSError:
            found, identities, listed = set(), {}, False
        if not listed:
            ok = False
        for pid, identity in frozen_ids.items():
            identities.setdefault(pid, identity)
        with lock:
            seen.update(found)
            seen.update(walked)
        live = {pid for pid in found if not _exited(pid, identities.get(pid))}
        new = live - stopped
        if new or not listed:
            for pid in new:
                _signal_pid(pid, signal.SIGSTOP)
                stopped.add(pid)
            if new:
                _signal_group(leader, signal.SIGSTOP, root_ids)
            seen_clean = False
            continue
        pending = {pid for pid in stopped if not _exited(pid, identities.get(pid))}
        pending.update(live)
        if pending:
            survivors = _kill_and_wait(pending, leader, root_ids, identities)
            stopped.clear()
            seen_clean = False
            if survivors:
                ok = False
            continue
        if not seen_clean:
            seen_clean = True
            continue
        _refuse_unaccounted(ok, set())
        return
    lingering = {pid for pid in stopped if not _exited(pid, frozen_ids.get(pid))}
    survivors = _kill_and_wait(lingering, leader, root_ids, frozen_ids) if lingering else set()
    _refuse_unaccounted(False, survivors)


def _watch_descendants(
    leader: int,
    seen: set[int],
    lock: threading.Lock,
    stop: threading.Event,
    memory_cap: int | None = None,
    memory_hit: threading.Event | None = None,
    process_cap: int | None = None,
    process_hit: threading.Event | None = None,
    account: _Account | None = None,
    sentinel: str | None = None,
) -> None:
    """Remember children, kill any that leave the group, and enforce the memory and process caps."""
    next_sample = 0.0
    while not stop.is_set():
        found: set[int] = set()
        if account is not None and sentinel:
            found = _sweep_escaped(leader, sentinel, account, seen, lock)
        else:
            try:
                found = _descendants(leader)
            except OSError:
                found = set()
            escaped: list[int] = []
            for pid in found:
                if pid == leader:
                    continue
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
            _kill_process_tree(leader, seen, lock, extra=found)
            return
        if memory_cap is not None and time.monotonic() >= next_sample:
            next_sample = time.monotonic() + _MEMORY_POLL_SECONDS
            used = _tree_resident_bytes(leader, found)
            if used is not None and used > memory_cap:
                if memory_hit is not None:
                    memory_hit.set()
                _kill_process_tree(leader, seen, lock, extra=found)
                return
        stop.wait(0.02)


def _kill_process_tree(
    leader: int,
    seen: set[int],
    lock: threading.Lock,
    extra: set[int] | None = None,
) -> None:
    """Kill the script's process group and any child that already left it."""
    try:
        os.killpg(leader, signal.SIGKILL)
    except OSError:
        pass
    _kill_pid(leader)
    with lock:
        targets = set(seen)
    if extra:
        targets.update(extra)
    if sys.platform != "darwin":
        try:
            targets.update(_descendants(leader))
        except OSError:
            pass
    for pid in targets:
        _kill_pid(pid)


def _close_child_pipes(proc: subprocess.Popen) -> None:
    for pipe in (proc.stdout, proc.stderr):
        if pipe is None:
            continue
        try:
            pipe.close()
        except OSError:
            pass


def _join_reaper(stop_watch: threading.Event | None, watcher: threading.Thread | None) -> bool:
    """Stop the watcher and wait until it has left the listing.

    False means it was still running. The caller then refuses a clean result
    so that thread cannot race the final stop.
    """
    if stop_watch is not None:
        stop_watch.set()
    if watcher is None:
        return True
    watcher.join(timeout=_REAP_EXIT_SECONDS)
    return not watcher.is_alive()


def _stop_child(proc: subprocess.Popen, seen: set[int], seen_lock: threading.Lock, *, reap_seconds: float = 5) -> None:
    """Kill a sandbox child and do not wait forever for it to disappear.

    The first signal is the process group. If the process is still alive, it
    is signalled again and the pipes are closed. The waits are bounded, so a
    child that does not die cannot hold the test until the job is cancelled.
    """
    _kill_process_tree(proc.pid, seen, seen_lock)
    try:
        proc.communicate(timeout=reap_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    _kill_process_tree(proc.pid, seen, seen_lock)
    _close_child_pipes(proc)
    try:
        proc.wait(timeout=reap_seconds)
    except subprocess.TimeoutExpired:
        pass


def _sandbox_runner() -> Path:
    """This install's sandbox_run.py. The child loads that file, not a name from the project."""

    import importlib.util

    spec = importlib.util.find_spec("aicad.sandbox_run")
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not isinstance(origin, str) or not origin:
        raise ScriptError("The sandbox runner is not a file in this install.")
    return Path(origin).resolve()


def _child_command(job: Path) -> list[str]:
    """Python command for one sandbox child.

    ``-P`` leaves the working directory off ``sys.path``. The runner argument
    is the installed file, so the child does not look the module up by name.
    """

    runner = str(_sandbox_runner())
    if sys.platform == "win32":
        return [sys.executable, "-P", "-B", "-u", "-c", _WINDOWS_CHILD_BOOT, str(job), runner]
    return [sys.executable, "-P", "-B", "-u", runner, str(job)]


class _DesignRecord:
    """The process that runs a design script's document calls and records them."""

    def __init__(self, workspace: Path, job: Path):
        self.workspace = workspace
        self.job = job
        self.proc: subprocess.Popen | None = None
        self.record: Path | None = None
        self._log: Path | None = None
        self._device = None
        self._inode = None

    def start(self) -> None:
        calls = self.job / "calls"
        calls.mkdir(mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix="aicad-design-checks-", suffix=".json")
        os.close(descriptor)
        self.record = Path(name)
        info = os.lstat(self.record)
        self._device = info.st_dev
        self._inode = info.st_ino
        log_fd = os.open(name + ".log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self._log = Path(name + ".log")
        env = _child_env(self.workspace, self.job, network=False)
        # This process runs the calls. It is not the confined script.
        # It still does not inherit loader variables or the parent PYTHONPATH.
        env.pop("AICAD_SANDBOX_CHILD", None)
        trusted = _trusted_pythonpath(self.workspace)
        if trusted:
            env["PYTHONPATH"] = trusted
        else:
            env.pop("PYTHONPATH", None)
        from aicad.thread_stack import child_inherits_geometry_stack

        try:
            with child_inherits_geometry_stack():
                self.proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-P",
                        "-B",
                        "-u",
                        str(_sandbox_runner()),
                        "--model",
                        str(self.job),
                        str(self.record),
                    ],
                    cwd=str(self.workspace),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fd,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        finally:
            os.close(log_fd)

    def failure_detail(self) -> str:
        proc = self.proc
        if proc is None or proc.poll() in (0, None) or self._log is None:
            return ""
        try:
            text = self._log.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if len(text) > 500:
            text = text[-500:]
        return text

    def _refuse(self) -> None:
        detail = self.failure_detail()
        message = "The design script could not record its checks."
        if detail:
            message = f"{message}\n{detail}"
        raise ScriptError(message)

    def merge(self, payload: dict) -> dict:
        if self.proc is None or self.record is None:
            raise ScriptError("The design script could not record its checks.")
        if self.proc.poll() not in (0, None):
            self._refuse()
        stop = self.job / "calls" / "STOP"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(stop, flags, 0o600)
        except FileExistsError:
            descriptor = None
        except OSError as exc:
            try:
                os.lstat(stop)
            except OSError:
                raise ScriptError("The design script could not record its checks.") from exc
            descriptor = None
        if descriptor is not None:
            os.close(descriptor)
        try:
            self.proc.communicate(timeout=wall_seconds())
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.communicate(timeout=5)
            self._refuse()
        if self.proc.returncode != 0:
            self._refuse()
        read_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            read_flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            read_flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(self.record, read_flags)
        except OSError as exc:
            raise ScriptError("The design script could not record its checks.") from exc
        try:
            info = os.fstat(descriptor)
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_dev != self._device
                or info.st_ino != self._inode
                or info.st_nlink != 1
                or info.st_size > 32_000_000
            ):
                raise ScriptError("The design script could not record its checks.")
            chunks: list[bytes] = []
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                chunks.append(block)
                if sum(len(part) for part in chunks) > 32_000_000:
                    raise ScriptError("The design script could not record its checks.")
        finally:
            os.close(descriptor)
        try:
            report = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ScriptError("The design script could not record its checks.") from exc
        if not isinstance(report, dict):
            raise ScriptError("The design script could not record its checks.")
        payload["checks"] = report.get("checks") if isinstance(report.get("checks"), list) else []
        payload["changed_parts"] = report.get("changed_parts") if isinstance(report.get("changed_parts"), list) else []
        payload["deleted_parts"] = report.get("deleted_parts") if isinstance(report.get("deleted_parts"), list) else []
        payload["views"] = report.get("views") if isinstance(report.get("views"), list) else []
        notes = report.get("notes") if isinstance(report.get("notes"), list) else []
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        payload["errors"] = [item for item in errors if isinstance(item, str)] + [
            item for item in notes if isinstance(item, str)
        ]
        return payload

    def close(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if self.record is not None:
            self.record.unlink(missing_ok=True)
            self.record = None
        if self._log is not None:
            self._log.unlink(missing_ok=True)
            self._log = None


def _spawn(workspace: Path, kind: str, script: str | None, network: bool) -> dict:
    ok, reason = sandbox_supported()
    if not ok:
        raise ScriptError(reason)
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    _refuse_multilink(workspace)
    from aicad.paths import is_reparse, output_dir

    private = output_dir(workspace, workspace / ".aicad", "Project data", ScriptError)
    tmp_dir = output_dir(workspace, private / "tmp", "Temporary files", ScriptError)
    jobs_dir = output_dir(workspace, private / "jobs", "Script jobs", ScriptError)
    cache_dir = None
    cache_path = private / "cache"
    if sys.platform == "win32" or is_reparse(cache_path):
        # On Windows this directory is created before the AppContainer grant
        # so the container can write it. A link that leaves the project is refused.
        cache_dir = output_dir(workspace, cache_path, "Library cache", ScriptError)
    job = jobs_dir / uuid.uuid4().hex
    try:
        job.mkdir(mode=0o700)
    except OSError as exc:
        raise ScriptError("Could not create a project folder, so the script was not run.") from exc
    spec: dict = {"kind": kind, "network": network}
    probe_root = None
    if kind == "probe":
        probe_root, spec["probe_read"], spec["probe_write"] = _make_probe(workspace)
    (job / "job.json").write_text(json.dumps(spec) + "\n", encoding="utf-8")
    if script is not None:
        (job / "script.py").write_text(script, encoding="utf-8")
    # -P is the same rule as PYTHONSAFEPATH: the project is the working
    # directory, and it must not be the first place Python imports from.
    # The runner is this install's file, not a module name looked up from
    # the working directory.
    command = _child_command(job)
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
        command = [*command_prefix(str(profile_path), workspace), *command]
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
    design_record: _DesignRecord | None = None
    account: _Account | None = None
    sentinel_fd: int | None = None
    sentinel_path: str | None = None
    keep_job = False
    try:
        if kind == "design":
            design_record = _DesignRecord(workspace, job)
            design_record.start()
        if sys.platform == "win32":
            from aicad.sandbox_windows import spawn as windows_spawn

            code, stdout, stderr, timed_out, memory_killed, _mechanism = windows_spawn(
                command,
                cwd=str(workspace),
                env=_child_env(workspace, job, network, tmp=tmp_dir, cache=cache_dir),
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
                detail = design_record.failure_detail() if design_record is not None else ""
                shutil.rmtree(job, ignore_errors=True)
                message = f"The script ran longer than {wall} seconds, so it was stopped."
                if detail:
                    message = f"{message}\n{detail}"
                raise ScriptError(message)
            if design_record is not None and design_record.proc is not None and design_record.proc.poll() not in (0, None):
                design_record._refuse()
            payload = _payload_from_result(job, code, stdout, stderr, memory_killed=memory_killed)
            if design_record is not None:
                payload = design_record.merge(payload)
            keep_job = True
            return payload
        if sys.platform.startswith("linux"):
            pids_cgroup = _prepare_pids_cgroup()
        env = _child_env(workspace, job, network, tmp=tmp_dir, cache=cache_dir)
        env.pop("AICAD_PIDS_CGROUP", None)
        if pids_cgroup is not None:
            env["AICAD_PIDS_CGROUP"] = str(pids_cgroup)
        popen_kwargs: dict = {
            "cwd": str(workspace),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "start_new_session": True,
        }
        if sys.platform == "darwin":
            from aicad.sandbox_macos import open_sentinel, process_identity

            opened = open_sentinel()
            if opened is None:
                raise ScriptError(
                    "The sandbox could not keep track of processes for this script, so it was not run."
                )
            sentinel_fd, sentinel_path = opened
            popen_kwargs["close_fds"] = True
            popen_kwargs["pass_fds"] = (sentinel_fd,)
            account = _Account()
        from aicad.thread_stack import child_inherits_geometry_stack

        with child_inherits_geometry_stack():
            proc = subprocess.Popen(command, **popen_kwargs)
        if account is not None and sentinel_path is not None:
            identity = process_identity(proc.pid)
            if identity is not None:
                account.add_ids({identity[0]})
        if pids_cgroup is not None:
            try:
                _require_pids_cgroup(pids_cgroup, proc.pid)
            except ScriptError:
                _stop_child(proc, seen, seen_lock)
                shutil.rmtree(job, ignore_errors=True)
                raise
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
            kwargs={"account": account, "sentinel": sentinel_path},
            name="aicad-sandbox-reap",
            daemon=True,
        )
        watcher.start()

        def sweep() -> None:
            if account is None or sentinel_path is None:
                return
            _sweep_escaped(proc.pid, sentinel_path, account, seen, seen_lock)

        try:
            stdout_b, stderr_b = _wait_script(proc, wall, sweep if account is not None else None)
        except subprocess.TimeoutExpired:
            if account is not None and not _join_reaper(stop_watch, watcher):
                account.fail()
            _stop_child(proc, seen, seen_lock)
            if account is not None and sentinel_path is not None:
                try:
                    _reap_macos(proc.pid, sentinel_path, account, seen, seen_lock)
                except ScriptError:
                    pass
            detail = design_record.failure_detail() if design_record is not None else ""
            shutil.rmtree(job, ignore_errors=True)
            message = f"The script ran longer than {wall} seconds, so it was stopped."
            if detail:
                message = f"{message}\n{detail}"
            raise ScriptError(message) from None
        if account is not None and sentinel_path is not None:
            if not _join_reaper(stop_watch, watcher):
                account.fail()
            _reap_macos(proc.pid, sentinel_path, account, seen, seen_lock)
        else:
            _kill_process_tree(proc.pid, seen, seen_lock)
        if design_record is not None and design_record.proc is not None and design_record.proc.poll() not in (0, None):
            design_record._refuse()
        payload = _payload_from_result(
            job,
            proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode("utf-8", "replace"),
            stderr_b.decode("utf-8", "replace"),
            memory_killed=memory_hit.is_set(),
            process_killed=process_hit.is_set(),
        )
        if design_record is not None:
            payload = design_record.merge(payload)
        keep_job = True
        return payload
    finally:
        if stop_watch is not None:
            stop_watch.set()
        if watcher is not None:
            watcher.join(timeout=1)
        _release_pids_cgroup(pids_cgroup)
        if profile_path is not None:
            profile_path.unlink(missing_ok=True)
        if sentinel_fd is not None and sentinel_path is not None:
            from aicad.sandbox_macos import close_sentinel

            close_sentinel(sentinel_fd, sentinel_path)
        if probe_root is not None:
            shutil.rmtree(probe_root, ignore_errors=True)
        if design_record is not None:
            design_record.close()
        if not keep_job:
            shutil.rmtree(job, ignore_errors=True)


def _died_from_limit_signal(code: int) -> bool:
    """True when ``code`` is the memory or CPU signal.

    The names are looked up only if this platform has them. A missing name
    is not a limit, and reading it must not raise.
    """

    if code >= 0:
        return False
    signals = {getattr(signal, name, None) for name in ("SIGKILL", "SIGXCPU")}
    signals.discard(None)
    return -code in signals


def _result_text(path: Path) -> str | None:
    """UTF-8 text of a regular result file. A link is not read.

    ``None`` means the file is not there. A symlink, junction, or file with
    another name raises ``ScriptError`` before a byte of that file is read.
    """
    from aicad.paths import is_reparse

    try:
        info = path.lstat()
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        raise ScriptError("The script result is a link and was not read.") from exc
    if stat.S_ISLNK(info.st_mode) or is_reparse(path):
        raise ScriptError("The script result is a link and was not read.")
    if not stat.S_ISREG(info.st_mode):
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise ScriptError("The script result is a link and was not read.") from exc
        if exc.errno == errno.ENOENT:
            return None
        raise ScriptError("The script result could not be read.") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink > 1
            or opened.st_ino != info.st_ino
            or opened.st_dev != info.st_dev
        ):
            raise ScriptError("The script result is a link and was not read.")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        try:
            return b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScriptError("The script result could not be read.") from exc
    finally:
        os.close(descriptor)


def _project_regular_file(root: Path, raw: str | Path, label: str) -> Path:
    """A regular file inside ``root``. A link is not opened."""
    from aicad.paths import is_reparse, shared_file

    root_resolved = Path(root).resolve()
    text = os.fspath(raw)
    if not text or "\x00" in text:
        raise ScriptError(f"{label} is outside the project folder {root_resolved}.")
    path = Path(text)
    if not path.is_absolute():
        path = root_resolved / path
    if is_reparse(path):
        raise ScriptError(f"{label} is a link and was not opened.")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ScriptError(f"{label} is outside the project folder {root_resolved}.") from exc
    if not _inside(resolved, root_resolved):
        raise ScriptError(f"{label} is outside the project folder {root_resolved}.")
    if is_reparse(resolved) or shared_file(resolved):
        raise ScriptError(f"{label} is a link and was not opened.")
    return resolved


def _refuse_result_paths(root: Path, payload: dict) -> None:
    """Refuse a picture path that leaves the project or is a link."""
    views: list[dict] = []
    listed = payload.get("views")
    if isinstance(listed, list):
        views.extend(item for item in listed if isinstance(item, dict))
    single = payload.get("view")
    if isinstance(single, dict):
        views.append(single)
    for view in views:
        raw = view.get("path")
        if isinstance(raw, str) and raw.strip():
            _project_regular_file(root, raw, "The script picture")


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
    try:
        result_text = _result_text(result_path)
    except ScriptError:
        shutil.rmtree(job, ignore_errors=True)
        raise
    if result_text is None:
        tail = stderr[-2000:]
        shutil.rmtree(job, ignore_errors=True)
        # SIGKILL is the memory cap and the parent's kill. SIGXCPU is the CPU
        # cap. Other signals, including an abort, are crashes. Windows has
        # neither name; a CPU stop on that platform is decided from the job
        # before this function runs.
        limit_signal = _died_from_limit_signal(code)
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
        payload = json.loads(result_text)
    except json.JSONDecodeError as exc:
        shutil.rmtree(job, ignore_errors=True)
        raise ScriptError(f"The script's result could not be read: {exc}") from exc
    if not isinstance(payload, dict):
        shutil.rmtree(job, ignore_errors=True)
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

        step = _project_regular_file(Path(workspace), str(payload["step"]), "The script geometry")
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
    try:
        _refuse_result_paths(root, payload)
    except ScriptError as exc:
        _drop_job(payload)
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
