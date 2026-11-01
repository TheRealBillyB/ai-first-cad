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

# Defaults. A test or the environment may set a lower value. The account
# config may raise the wall clock for one project up to WALL_SECONDS_CAP.
# A file in the project, the environment, and a script cannot raise it.
WALL_SECONDS = 120
WALL_SECONDS_CAP = 600
CPU_SECONDS = 60
MEMORY_BYTES = 4 * 1024 * 1024 * 1024
FILE_BYTES = 512 * 1024 * 1024
DISK_BYTES = FILE_BYTES
SCRIPT_MAX_CHARS = 200_000
STDOUT_MAX = 64_000
_MAX_SCRIPT_LOG_BYTES = 1_000_000
# The parent keeps only this much of a script's pipes or output files.
# The tail is what an error report shows. The rest is not stored.
_SCRIPT_OUTPUT_CAP = 1_000_000

_REAL_IMPORT = builtins.__import__
_GUARD = False
_REAL_OS_OPEN = None
_NULL_DEVICE: str | None = None
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


def _wall_file() -> Path:
    return config_dir() / "script_wall.json"


def _read_wall_table() -> dict:
    path = _wall_file()
    try:
        from aicad.file_access import read_text

        data = json.loads(read_text(path, maximum=1_000_000))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"projects": {}}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        return {"projects": {}}
    return data


def _write_wall_table(data: dict) -> None:
    path = _wall_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    from aicad.paths import publish_text

    publish_text(path, json.dumps(data, indent=2) + "\n", root=path.parent, private=True)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def project_wall_seconds(workspace: str | Path) -> int | None:
    """The wall clock chosen for this project, or None when it stays at the default.

    A value above the cap is read as the cap. A project file is not consulted.
    """

    root = str(Path(workspace).resolve())
    entry = _read_wall_table()["projects"].get(root)
    if not isinstance(entry, dict):
        return None
    value = entry.get("wall_seconds")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return min(value, WALL_SECONDS_CAP)


def set_project_wall_seconds(workspace: str | Path, seconds: int) -> dict:
    """Record how long a design script in this project may run.

    The value lives in the account config, outside the project. A script
    cannot call this. The cap stays in force even if the file is edited.
    """

    from aicad.sandbox_state import in_sandbox

    if in_sandbox():
        raise ScriptError("A script cannot change how long a script may run.")
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1 or seconds > WALL_SECONDS_CAP:
        raise ScriptError(
            f"The script time limit must be a whole number of seconds from 1 to {WALL_SECONDS_CAP}."
        )
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    data = _read_wall_table()
    data["projects"][str(root)] = {"wall_seconds": seconds}
    _write_wall_table(data)
    return {
        "workspace": str(root),
        "wall_seconds": seconds,
        "default": WALL_SECONDS,
        "cap": WALL_SECONDS_CAP,
    }


def wall_seconds(workspace: str | Path | None = None) -> int:
    """Seconds a script may run. The environment can lower this and cannot raise it."""

    limit = WALL_SECONDS
    if workspace is not None:
        chosen = project_wall_seconds(workspace)
        if chosen is not None:
            limit = chosen
    return _capped("AICAD_SCRIPT_WALL_SECONDS", limit, limit)


def cpu_seconds() -> int:
    return _capped("AICAD_SCRIPT_CPU_SECONDS", CPU_SECONDS, CPU_SECONDS)


def memory_bytes() -> int:
    megabytes = _capped("AICAD_SCRIPT_MEMORY_MB", MEMORY_BYTES // (1024 * 1024), MEMORY_BYTES // (1024 * 1024))
    return megabytes * 1024 * 1024


def file_bytes() -> int:
    """File-size cap for one script. An environment value cannot raise it."""
    return _capped("AICAD_SCRIPT_FILE_BYTES", FILE_BYTES, FILE_BYTES)


def disk_bytes() -> int:
    """Cumulative bytes one script may write. An environment value cannot raise it."""
    return _capped("AICAD_SCRIPT_DISK_BYTES", DISK_BYTES, DISK_BYTES)


def config_dir() -> Path:
    override = os.environ.get("AICAD_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "aicad"


def _opt_in_file() -> Path:
    return config_dir() / "design_scripts.json"


def _read_opt() -> dict:
    path = _opt_in_file()
    try:
        from aicad.file_access import read_text

        data = json.loads(read_text(path, maximum=1_000_000))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"projects": {}}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        return {"projects": {}}
    return data


def _write_opt(data: dict) -> None:
    path = _opt_in_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    from aicad.paths import publish_text

    publish_text(path, json.dumps(data, indent=2) + "\n", root=path.parent, private=True)
    try:
        path.chmod(0o600)
    except OSError:
        pass


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
    """Refuse a design-script path that leaves the project.

    No-op unless this process is the confined script, or it is running a call
    that script asked for. A call the script asked for stays in the project.
    It does not use the library directories the confined script may read.
    """
    from aicad.sandbox_state import in_design_call, in_sandbox

    script_call = in_design_call()
    if not in_sandbox() and not script_call:
        return
    root = Path(workspace).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    if script_call:
        from aicad.paths import _lexical_inside

        if _lexical_inside(candidate, root) and _inside(candidate, root):
            return
    elif _inside(candidate, root) or (not writing and _under(candidate, _read_roots())):
        return
    action = "write" if writing else "read"
    from aicad.paths import hide_project_path

    message = (
        f"The script tried to {action} {candidate}, which is outside the project folder {root}. "
        "A design script can only use files inside that folder."
    )
    raise ScriptError(hide_project_path(message, root))


def design_script_status(workspace: str | Path, network_once: bool = False, network_all: bool = False) -> dict:
    root = Path(workspace).resolve()
    if network_all:
        network = "on"
    elif network_once:
        network = "next run"
    else:
        network = "off"
    from aicad.paths import hide_project_path

    return {
        "enabled": design_scripts_enabled(root),
        "warning": WARNING,
        "enable": hide_project_path(f"aicad allow-scripts --workspace {root}", root),
        "network": network,
        "wall_seconds": wall_seconds(root),
        "log": hide_project_path(str(root / ".aicad" / "script_log.jsonl"), root),
    }


def script_log_path(workspace: Path) -> Path:
    return Path(workspace) / ".aicad" / "script_log.jsonl"


def _read_log_tail(path: Path, maximum: int) -> str:
    """The end of a log, at most ``maximum`` bytes. The rest is not read."""
    from aicad.file_access import read_tail

    try:
        data = read_tail(path, maximum)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


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
    from aicad.paths import hide_project_path

    shown = hide_project_path(error, root) if isinstance(error, str) else error
    row = {"time": _now(), "kind": "design", "ok": ok, "error": shown, "script": script}
    line = json.dumps(row) + "\n"
    try:
        existing = _read_log_tail(path, _MAX_SCRIPT_LOG_BYTES) if path.is_file() else ""
    except OSError:
        return
    try:
        publish_text(path, existing + line, root=root)
    except OSError:
        return


def _hide_script_report(payload: dict, root: Path) -> None:
    """Remove the absolute project path from a script's errors and output."""
    from aicad.paths import hide_project_path

    for key in ("error", "stdout", "stderr"):
        value = payload.get(key)
        if isinstance(value, str):
            payload[key] = hide_project_path(value, root)
    errors = payload.get("errors")
    if isinstance(errors, list):
        payload["errors"] = [hide_project_path(item, root) if isinstance(item, str) else item for item in errors]


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
    global _GUARD, _NULL_DEVICE, _REAL_OS_OPEN
    if _GUARD:
        return
    import io

    real_open = builtins.open
    real_os_open = os.open
    _REAL_OS_OPEN = real_os_open
    _NULL_DEVICE = os.devnull
    root = workspace.resolve()

    def py_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            writing = any(flag in str(mode) for flag in ("w", "a", "x", "+"))
            path = Path(os.fsdecode(file))
            if not path.is_absolute():
                path = Path.cwd() / path
            _refuse_project_link(path, root, read_roots, writing)
            if writing:
                _refuse_library_config_write(path)
        return real_open(file, mode, *args, **kwargs)

    def os_open(path, flags, mode=0o777, *, dir_fd=None):
        if not isinstance(path, int):
            writing = bool(flags & _WRITE_FLAGS)
            file_path = Path(os.fsdecode(path))
            if not file_path.is_absolute():
                file_path = Path.cwd() / file_path
            _refuse_project_link(file_path, root, read_roots, writing)
            if writing:
                _refuse_library_config_write(file_path)
        return real_os_open(path, flags, mode, dir_fd=dir_fd)

    builtins.open = py_open
    io.open = py_open  # type: ignore[assignment]
    os.open = os_open  # type: ignore[assignment]
    _GUARD = True


def open_null_device(flags: int) -> int:
    """Open a sink so a reader can discard native logs.

    The script file check wraps ``os.open``. The STEP reader uses this instead,
    and only for the null device recorded when that check was installed. A
    confined Windows process cannot open that device, so the log is discarded
    in the temporary directory instead. A script that names the device, or any
    other path, still goes through the check.
    """
    target = os.devnull
    try:
        if (
            _GUARD
            and _REAL_OS_OPEN is not None
            and _NULL_DEVICE is not None
            and os.path.normcase(os.fspath(target)) == os.path.normcase(_NULL_DEVICE)
        ):
            return _REAL_OS_OPEN(target, flags)
        return os.open(target, flags)
    except OSError:
        raw, name = tempfile.mkstemp(prefix="aicad-discard-")
        os.close(raw)
        temporary = getattr(os, "O_TEMPORARY", 0)
        return os.open(name, flags | temporary)


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


def _use_private_library_home(workspace: Path, private: Path) -> None:
    """Point library config at an empty folder. The account home is unchanged.

    Gmsh, fontconfig, and ezdxf read a fixed path under the home directory.
    That home is now this folder, which is emptied first so a project cannot
    leave a config there. The process is started with the account profile.
    Python on Windows resolves ``~`` from ``USERPROFILE``, so the running
    script uses this folder. Plugin consent uses the home remembered before
    this replacement.
    """
    from aicad.paths import is_reparse, output_dir, remove_tree

    folder = private / "library-config"
    if is_reparse(folder):
        folder.unlink()
    elif folder.exists():
        remove_tree(folder)
    created = output_dir(workspace, private / "library-config", "Library config", ScriptError)
    global _LIBRARY_HOME
    _LIBRARY_HOME = str(created)
    os.environ["HOME"] = _LIBRARY_HOME
    os.environ["XDG_CONFIG_HOME"] = _LIBRARY_HOME
    os.environ["MPLCONFIGDIR"] = _LIBRARY_HOME
    if sys.platform == "win32":
        os.environ["USERPROFILE"] = _LIBRARY_HOME
    # A lookup of a directory that is not there yet is not a script error.
    ctypes.set_errno(0)


_LIBRARY_HOME: str | None = None


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
    from aicad.sandbox_state import mark_sandboxed, remember_account_home

    mark_sandboxed()
    remember_account_home(Path.home())
    seal_sandbox_plugins()
    _refuse_foreign_kill()
    workspace = workspace.resolve()
    from aicad.paths import output_dir

    private = output_dir(workspace, workspace / ".aicad", "Project data", ScriptError)
    temporary = output_dir(workspace, private / "tmp", "Temporary files", ScriptError)
    _use_private_library_home(workspace, private)
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
            from aicad.file_access import open_write

            fd = open_write(result_path.parent, result_path.name)
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


_LIBRARY_PREFIXES = ("CSF_", "GMSH_", "FONTCONFIG_")
_LIBRARY_NAMES = frozenset(
    {
        "CASROOT",
        "MMGT_OPT",
        "MMGT_CLEAR",
        "MMGT_REENTRANT",
        "FREETYPE_PROPERTIES",
        "MPLCONFIGDIR",
    }
)


def library_env_steers(name: str) -> bool:
    """True when ``name`` points a geometry or font library at a config."""

    upper = name.upper()
    if upper in _LIBRARY_NAMES:
        return True
    return upper.startswith(_LIBRARY_PREFIXES)


def _refuse_library_config_write(path: Path) -> None:
    """A design script cannot write the library configuration directory.

    Libraries may still write a cache there. A call that comes from the
    script, rather than from one of those libraries, is refused.
    """

    home = _LIBRARY_HOME
    if not home:
        return
    try:
        resolved = path.resolve()
        root = Path(home).resolve()
    except OSError:
        return
    if resolved != root and root not in resolved.parents:
        return
    if _script_writes_directly():
        raise ScriptError("A design script cannot write the library configuration directory.")


def _script_writes_directly() -> bool:
    """True when the script called ``open`` itself, not a geometry library."""

    library_roots = {"matplotlib", "ezdxf", "gmsh", "fontTools", "build123d", "OCP", "PIL"}
    saw_script = False
    frame = sys._getframe()
    while frame is not None:
        filename = frame.f_code.co_filename
        module = str(frame.f_globals.get("__name__", ""))
        if filename in {"<aicad-design>", "<aicad-script>"}:
            saw_script = True
        root = module.split(".", 1)[0]
        if root in library_roots:
            return False
        frame = frame.f_back
    return saw_script


_LOCKED_ENV = frozenset(
    {
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "MPLCONFIGDIR",
        "FONTCONFIG_FILE",
        "FONTCONFIG_PATH",
        "EZDXF_CONFIG_FILE",
        "TMPDIR",
        "TMP",
        "TEMP",
    }
)


def lock_library_environment() -> None:
    """Keep library config paths from being pointed somewhere else.

    Setting a locked name to the value it already has is left alone, so a
    later refresh of the packaged path still works. A different value is refused.
    """

    if getattr(os.environ, "_aicad_locked", False):
        return
    inner = os.environ

    class _Locked(dict):
        _aicad_locked = True

        def __getitem__(self, key):
            return inner[key]

        def __setitem__(self, key, value):
            if _env_name_locked(key):
                if inner.get(key) == value:
                    return
                raise PermissionError(1, "Operation not permitted")
            inner[key] = value

        def __delitem__(self, key):
            if _env_name_locked(key):
                raise PermissionError(1, "Operation not permitted")
            del inner[key]

        def __iter__(self):
            return iter(inner)

        def __len__(self):
            return len(inner)

        def __contains__(self, key):
            return key in inner

        def copy(self):
            return inner.copy()

        def get(self, key, default=None):
            return inner.get(key, default)

        def items(self):
            return inner.items()

        def keys(self):
            return inner.keys()

        def values(self):
            return inner.values()

        def setdefault(self, key, default=None):
            if key in inner:
                return inner[key]
            self[key] = default
            return inner[key]

        def pop(self, key, *default):
            if _env_name_locked(key):
                raise PermissionError(1, "Operation not permitted")
            if default:
                return inner.pop(key, default[0])
            return inner.pop(key)

        def update(self, other=(), **kwargs):
            if isinstance(other, dict):
                pairs = other.items()
            else:
                pairs = other
            for key, value in pairs:
                self[key] = value
            for key, value in kwargs.items():
                self[key] = value

    os.environ = _Locked()  # type: ignore[assignment]
    real_putenv = os.putenv

    def putenv(name, value):
        if _env_name_locked(name) and os.environ.get(name) != value:
            raise PermissionError(1, "Operation not permitted")
        if _env_name_locked(name):
            return None
        return real_putenv(name, value)

    os.putenv = putenv  # type: ignore[assignment]
    _seal_environ_binding()
    snapshot = _snapshot_locked_environment()
    read_env, write_env = _environment_access()
    _seal_imported_environment()
    _watch_library_environment(snapshot, read_env, write_env)


def _seal_environ_binding() -> None:
    """Refuse replacing the environment mapping or clearing a locked name.

    A geometry library may write its cache under the private library-config
    folder. Pointing that cache somewhere else, including by replacing the
    environment mapping or clearing a locked name, is refused.
    """

    if not getattr(type(os), "_aicad_environ_sealed", False):
        module_type = type(os)

        class _Sealed(module_type):
            _aicad_environ_sealed = True

            def __setattr__(self, name, value):
                if name in {"environ", "environb"}:
                    raise PermissionError(1, "Operation not permitted")
                return module_type.__setattr__(self, name, value)

        os.__class__ = _Sealed
    real_unsetenv = getattr(os, "unsetenv", None)
    if real_unsetenv is not None and not getattr(real_unsetenv, "_aicad_unsetenv", False):

        def unsetenv(name):
            if _env_name_locked(name):
                raise PermissionError(1, "Operation not permitted")
            return real_unsetenv(name)

        unsetenv._aicad_unsetenv = True  # type: ignore[attr-defined]
        os.unsetenv = unsetenv  # type: ignore[assignment]


_ENV_CALLS = frozenset(
    {
        "setenv",
        "unsetenv",
        "putenv",
        "_putenv",
        "_putenv_s",
        "_wputenv",
        "_wputenv_s",
        "SetEnvironmentVariableA",
        "SetEnvironmentVariableW",
    }
)


def _seal_imported_environment() -> None:
    """Refuse a library-config change made through ``ctypes`` on an imported module.

    Geometry code keeps using libraries it already loaded. A new library load
    from a design script cannot change the locked names.
    """

    import ctypes

    if getattr(ctypes, "_aicad_env_sealed", False):
        return

    def guard(library):
        if library is None or getattr(library, "_aicad_env_guard", False):
            return library

        class _Guard:
            _aicad_env_guard = True

            def __init__(self, inner):
                object.__setattr__(self, "_inner", inner)

            def __getattr__(self, name):
                value = getattr(self._inner, name)
                if name in _ENV_CALLS and callable(value):
                    return _refuse_locked_env(value)
                return value

        return _Guard(library)

    def factory(real):
        def load(*args, **kwargs):
            return guard(real(*args, **kwargs))

        load._aicad_sealed_factory = True
        return load

    for class_name in ("CDLL", "PyDLL", "WinDLL", "OleDLL"):
        real = getattr(ctypes, class_name, None)
        if real is None or getattr(real, "_aicad_sealed_factory", False):
            continue
        setattr(ctypes, class_name, factory(real))
    for loader_name in ("cdll", "windll", "oledll", "pydll"):
        loader = getattr(ctypes, loader_name, None)
        if loader is None:
            continue
        dlltype = getattr(loader, "_dlltype", None)
        if dlltype is not None and hasattr(ctypes, getattr(dlltype, "__name__", "")):
            loader._dlltype = getattr(ctypes, dlltype.__name__)
        libraries = getattr(loader, "_libraries", None)
        if isinstance(libraries, dict):
            for key, library in list(libraries.items()):
                libraries[key] = guard(library)
    ctypes._aicad_env_sealed = True


def _refuse_locked_env(func):
    def call(*args, **kwargs):
        key = args[0] if args else kwargs.get("name", kwargs.get("lpName"))
        if isinstance(key, bytes):
            key = key.decode("utf-8", "replace")
        if isinstance(key, str) and _env_name_locked(key):
            raise PermissionError(1, "Operation not permitted")
        return func(*args, **kwargs)

    return call


def _environment_access():
    """Read and write the process environment below Python's ``os.environ``."""

    import ctypes

    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        read = kernel.GetEnvironmentVariableW
        write = kernel.SetEnvironmentVariableW
        read.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        read.restype = ctypes.c_uint32
        write.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        write.restype = ctypes.c_int

        def get(key: str) -> str | None:
            size = read(key, None, 0)
            if size == 0:
                return None
            buffer = ctypes.create_unicode_buffer(size)
            read(key, buffer, size)
            return buffer.value

        def set(key: str, value: str) -> None:
            write(key, value)

        return get, set
    libc = ctypes.CDLL(None, use_errno=True)
    getenv = libc.getenv
    setenv = libc.setenv
    getenv.argtypes = [ctypes.c_char_p]
    getenv.restype = ctypes.c_char_p
    setenv.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    setenv.restype = ctypes.c_int

    def get(key: str) -> str | None:
        raw = getenv(key.encode())
        if raw is None:
            return None
        return raw.decode("utf-8", "replace")

    def set(key: str, value: str) -> None:
        setenv(key.encode(), value.encode(), 1)

    return get, set


def _snapshot_locked_environment() -> dict[str, str]:
    found = {}
    for key, value in list(os.environ.items()):
        if _env_name_locked(key) and isinstance(value, str):
            found[key] = value
    return found


def _watch_library_environment(snapshot: dict[str, str], read, write) -> None:
    """Put the locked names back if something changes them after the snapshot."""

    if not snapshot or getattr(_watch_library_environment, "started", False):
        return
    _watch_library_environment.started = True

    def restore() -> None:
        for key, value in snapshot.items():
            if read(key) != value:
                write(key, value)
            if os.environ.get(key) != value:
                os.environ[key] = value

    def loop() -> None:
        try:
            # Stop once this process is no longer the locked script environment.
            # A test that restores os.environ must not keep resetting TEMP.
            while getattr(os.environ, "_aicad_locked", False):
                restore()
                time.sleep(0.05)
        finally:
            _watch_library_environment.started = False

    threading.Thread(target=loop, name="aicad-library-env", daemon=True).start()


def _env_name_locked(name: str) -> bool:
    upper = str(name).upper()
    if upper in _LOCKED_ENV or library_env_steers(str(name)):
        return True
    return False


def scrub_library_environment() -> None:
    """Drop geometry and font configuration variables from this process.

    Matplotlib then uses a fresh directory instead of the account home.
    Fontconfig uses the file shipped with aicad. A design script keeps the
    empty library directory installed for that script.
    """

    owned = _LIBRARY_HOME
    for key in list(os.environ):
        if library_env_steers(key):
            os.environ.pop(key, None)
    from aicad.paths import packaged_fontconfig_file

    os.environ["FONTCONFIG_FILE"] = str(packaged_fontconfig_file())
    if owned and Path(owned).is_dir():
        os.environ["MPLCONFIGDIR"] = owned
    else:
        os.environ["MPLCONFIGDIR"] = str(_private_matplotlib_dir())


def _private_matplotlib_dir() -> Path:
    """A new config directory. It is not the account home and not a project file.

    The directory is opened by its handle. The returned path stays on that
    directory when the system can name an open directory.
    """
    global _MATPLOTLIB_CONFIG
    if _MATPLOTLIB_CONFIG is not None:
        return _MATPLOTLIB_CONFIG
    _MATPLOTLIB_CONFIG = open_matplotlib_directory().public
    return _MATPLOTLIB_CONFIG


class MatplotlibDirectory:
    """One config directory kept open for the process."""

    def __init__(self, original: Path, public: Path, descriptor: int, windows_handle: int | None) -> None:
        self.original = original
        self.public = public
        self.descriptor = descriptor
        self.windows_handle = windows_handle


def open_matplotlib_directory() -> MatplotlibDirectory:
    """Create a private config directory and open it before returning its path."""
    import tempfile

    original = Path(tempfile.mkdtemp(prefix="aicad-mpl-"))
    try:
        if os.name == "nt":
            handle = _open_windows_config_directory(original)
            return MatplotlibDirectory(original, original, -1, handle)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(os.fspath(original), flags)
    except Exception:
        shutil.rmtree(original, ignore_errors=True)
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(errno.ENOTDIR, "not a directory", os.fspath(original))
        public = _config_directory_path(descriptor, original)
        return MatplotlibDirectory(original, public, descriptor, None)
    except Exception:
        os.close(descriptor)
        shutil.rmtree(original, ignore_errors=True)
        raise


def close_matplotlib_directory(held: MatplotlibDirectory) -> None:
    """Close a config directory and remove it when its name still refers to it."""
    info = None
    current: Path | None = None
    if held.descriptor >= 0:
        try:
            info = os.fstat(held.descriptor)
        except OSError:
            info = None
        current = _directory_path_from_descriptor(held.descriptor)
        os.close(held.descriptor)
        held.descriptor = -1
    if held.windows_handle:
        _close_windows_config_handle(held.windows_handle)
        held.windows_handle = None
    if current is not None and info is not None:
        try:
            found = os.lstat(current)
        except OSError:
            found = None
        if found is not None and stat.S_ISDIR(found.st_mode) and os.path.samestat(info, found):
            shutil.rmtree(current, ignore_errors=True)
        try:
            if held.original.is_symlink():
                held.original.unlink()
        except OSError:
            pass
        return
    shutil.rmtree(held.original, ignore_errors=True)


def _config_directory_path(descriptor: int, original: Path) -> Path:
    proc = Path(f"/proc/self/fd/{descriptor}")
    try:
        if proc.parent.is_dir():
            return proc
    except OSError:
        pass
    volfs = _volfs_config_directory(descriptor)
    if volfs is not None:
        return volfs
    return original


def _volfs_config_directory(descriptor: int) -> Path | None:
    """A directory path addressed by its id, when listing and creating both work."""
    if sys.platform != "darwin":
        return None
    try:
        info = os.fstat(descriptor)
    except OSError:
        return None
    directory = Path(f"/.vol/{info.st_dev}/{info.st_ino}")
    probe = f".aicad-probe-{os.getpid()}-{uuid.uuid4().hex}"
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        raw = os.open(probe, flags, 0o600, dir_fd=descriptor)
    except OSError:
        return None
    os.close(raw)
    try:
        listed = probe in os.listdir(directory)
    except OSError:
        listed = False
    try:
        os.unlink(probe, dir_fd=descriptor)
    except OSError:
        pass
    if not listed:
        return None
    return directory


def _directory_path_from_descriptor(descriptor: int) -> Path | None:
    proc = f"/proc/self/fd/{descriptor}"
    try:
        if os.path.isdir(os.path.dirname(proc)):
            return Path(os.readlink(proc))
    except OSError:
        pass
    if sys.platform != "darwin":
        return None
    import fcntl

    # fcntl returns a new bytes object of the same length. It does not write
    # the path back into the buffer it was given. The length is PATH_MAX.
    command = getattr(fcntl, "F_GETPATH", 50)
    try:
        result = fcntl.fcntl(descriptor, command, bytes(1024))
    except OSError:
        return None
    if isinstance(result, str):
        raw = os.fsencode(result)
    elif isinstance(result, (bytes, bytearray)):
        raw = bytes(result)
    else:
        return None
    raw = raw.split(b"\x00", 1)[0]
    if not raw:
        return None
    return Path(os.fsdecode(raw))


def _open_windows_config_directory(original: Path) -> int:
    """Keep the directory open without sharing delete, so its name stays put."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create.restype = ctypes.c_void_p
    handle = create(
        os.fspath(original),
        0x1 | 0x80,
        0x1 | 0x2,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if not handle or handle == invalid:
        error = ctypes.get_last_error()
        raise OSError(None, None, os.fspath(original), error or errno.EIO)
    try:
        if _windows_config_handle_is_reparse(kernel32, handle):
            raise OSError(errno.ELOOP, "is a link", os.fspath(original))
        kept = int(handle)
        handle = None
        return kept
    finally:
        if handle:
            _close_windows_config_handle(handle)


def _windows_config_handle_is_reparse(kernel32, handle) -> bool:
    class _FileTime(ctypes.Structure):
        _fields_ = (("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32))

    class _ByHandle(ctypes.Structure):
        _fields_ = (
            ("dwFileAttributes", ctypes.c_uint32),
            ("ftCreationTime", _FileTime),
            ("ftLastAccessTime", _FileTime),
            ("ftLastWriteTime", _FileTime),
            ("dwVolumeSerialNumber", ctypes.c_uint32),
            ("nFileSizeHigh", ctypes.c_uint32),
            ("nFileSizeLow", ctypes.c_uint32),
            ("nNumberOfLinks", ctypes.c_uint32),
            ("nFileIndexHigh", ctypes.c_uint32),
            ("nFileIndexLow", ctypes.c_uint32),
        )

    info = _ByHandle()
    get = kernel32.GetFileInformationByHandle
    get.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ByHandle)]
    get.restype = ctypes.c_int
    if not get(handle, ctypes.byref(info)):
        raise OSError(errno.EIO, "could not read the directory")
    if not info.dwFileAttributes & 0x10:
        raise OSError(errno.ENOTDIR, "not a directory")
    return bool(info.dwFileAttributes & 0x400)


def _close_windows_config_handle(handle) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    close.restype = ctypes.c_int
    close(handle)


_MATPLOTLIB_CONFIG: Path | None = None


def scrubbed_environment(workspace: Path | None = None) -> dict[str, str]:
    """An environment for a child. Loader and startup variables are omitted."""

    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith("LD_") or upper.startswith("DYLD_") or upper == "PATH":
            continue
        if upper in _DROPPED_ENV or library_env_steers(key):
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
    """Environment for CalculiX, ffmpeg, a slicer, and other helper programs.

    The helper gets the system PATH and the variables Windows needs to start
    a process. It does not get the rest of the parent environment.
    """

    env: dict[str, str] = {
        "PATH": _system_path(),
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("LANG", "LC_ALL", "LC_CTYPE", "TZ"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    _restore_os_environment(env)
    return env


class _HelperResult:
    """The return code and text from one confined helper."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


_HELPER_BOOT = """
import json, os, sys
from pathlib import Path
reads = [Path(item) for item in json.loads(sys.argv[1])]
work = Path.cwd()
from aicad.sandbox import _landlock
try:
    _landlock(work, False, reads)
except Exception:
    os._exit(127)
os.execv(sys.argv[2], sys.argv[2:])
"""


def _helper_read_paths(command: list[str]) -> list[Path]:
    """System locations a helper may read, plus the directory that holds the program.

    When the command is the interpreter and a script, the script's folder is
    included. Later arguments are not: a path named there is not made readable.
    """

    roots = _read_roots()
    if not command:
        return roots
    program = Path(command[0])
    try:
        if program.is_file():
            parent = program.resolve().parent
            roots.append(parent)
            # A packaged solver keeps its libraries beside the program directory.
            for name in ("lib", "lib64"):
                library = parent.parent / name
                if library.is_dir():
                    roots.append(library)
    except OSError:
        pass
    names = {os.path.normcase(os.path.normpath(sys.executable))}
    base = getattr(sys, "_base_executable", "") or ""
    if base:
        names.add(os.path.normcase(os.path.normpath(base)))
    try:
        own = os.path.normcase(os.path.normpath(str(program)))
    except (OSError, ValueError):
        return roots
    if own not in names:
        return roots
    for arg in command[1:]:
        if not arg or arg.startswith("-"):
            continue
        candidate = Path(arg)
        try:
            if candidate.is_file():
                roots.append(candidate.resolve().parent)
        except OSError:
            pass
        break
    return roots


def run_confined_helper(command: list[str], *, env: dict[str, str], timeout: float, cwd=None, preexec_fn=None, pass_fds=(), close_fds=True):
    """Run one helper under the same file limits as a design script.

    The helper may read the locations needed to start the program. It may
    read and write ``cwd``. It is not started when those limits cannot be
    applied.
    """

    import subprocess

    if not command:
        raise ScriptError("The helper was not given a program, so it was not run.")
    work = Path(cwd) if cwd is not None else Path.cwd()
    reads = _helper_read_paths(command)
    if sys.platform == "win32":
        return _run_confined_helper_windows(command, env=env, timeout=timeout, cwd=work, reads=reads)
    if sys.platform == "darwin":
        return _run_confined_helper_macos(
            command,
            env=env,
            timeout=timeout,
            cwd=work,
            reads=reads,
            preexec_fn=preexec_fn,
            pass_fds=pass_fds,
            close_fds=close_fds,
        )
    boot = [sys.executable, "-c", _HELPER_BOOT, json.dumps([str(path) for path in reads]), *command]
    kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "stdin": subprocess.DEVNULL,
        "timeout": timeout,
        "check": False,
        "env": env,
        "cwd": str(work),
        "close_fds": close_fds,
    }
    if pass_fds:
        kwargs["pass_fds"] = pass_fds
        kwargs["close_fds"] = True
    if preexec_fn is not None:
        kwargs["preexec_fn"] = preexec_fn
    completed = subprocess.run(boot, **kwargs)
    return _HelperResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def _helper_write_root(cwd: Path, pass_fds) -> Path:
    """The folder the helper writes in, under the name it has now.

    The path recorded for that folder can have been replaced. The open
    directory is the one the helper uses.
    """

    if not pass_fds:
        return cwd
    import stat

    from aicad.tool_directory import _directory_from_handle

    for raw in pass_fds:
        try:
            fd = int(raw)
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                continue
        except (OSError, TypeError, ValueError):
            continue
        found = _directory_from_handle(fd)
        if found is None:
            continue
        try:
            if found.is_dir() and not found.is_symlink():
                return found
        except OSError:
            continue
    return cwd


def _run_confined_helper_macos(command, *, env, timeout, cwd: Path, reads: list[Path], preexec_fn, pass_fds, close_fds):
    import subprocess
    import tempfile

    from aicad.sandbox_macos import build_profile, command_prefix

    cwd = _helper_write_root(cwd, pass_fds)
    # Scratch files stay in the folder the helper may write. The process
    # temporary directory is not that folder.
    env = dict(env)
    for name in ("TMPDIR", "TMP", "TEMP"):
        env[name] = str(cwd)
    profile = build_profile([str(path) for path in reads], [str(cwd)], network=False)
    directory = _control_directory()
    try:
        handle = tempfile.NamedTemporaryFile(prefix="aicad-helper-", suffix=".sb", dir=directory, delete=False)
        handle.write(profile.encode("utf-8"))
        handle.close()
        wrapped = [*command_prefix(handle.name, cwd), *command]
        kwargs = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "stdin": subprocess.DEVNULL,
            "timeout": timeout,
            "check": False,
            "env": env,
            "cwd": str(cwd),
            "close_fds": close_fds,
        }
        if pass_fds:
            kwargs["pass_fds"] = pass_fds
            kwargs["close_fds"] = True
        if preexec_fn is not None:
            kwargs["preexec_fn"] = preexec_fn
        completed = subprocess.run(wrapped, **kwargs)
    finally:
        from aicad.paths import remove_tree

        remove_tree(directory, ignore_errors=True)
    return _HelperResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def _windows_helper_command(command: list[str]) -> list[str]:
    """Start a script with the interpreter. A batch file goes through the shell."""

    program = command[0]
    folded = program.lower()
    if folded.endswith(".py"):
        return [sys.executable, *command]
    if folded.endswith(".cmd") or folded.endswith(".bat"):
        shell = os.environ.get("COMSPEC", "cmd.exe")
        return [shell, "/c", program, *command[1:]]
    return list(command)


def _windows_helper_environment(env: dict[str, str], cwd: Path) -> dict[str, str]:
    """Environment Windows needs to start a confined helper.

    Temporary files and the profile stay in the helper folder. The account
    profile is not copied. ``SystemRoot`` and the other session names are
    required for the process to start.
    """

    merged = dict(env)
    for name in _WINDOWS_SESSION:
        value = os.environ.get(name)
        if value and name not in merged:
            merged[name] = value
    location = str(cwd)
    for name in ("TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        merged[name] = location
    return merged


def _run_confined_helper_windows(command, *, env, timeout, cwd: Path, reads: list[Path]):
    import subprocess

    from aicad.sandbox_windows import spawn

    command = _windows_helper_command(list(command))
    env = _windows_helper_environment(env, cwd)
    stdout_path = cwd / "helper-stdout.txt"
    stderr_path = cwd / "helper-stderr.txt"
    code, stdout, stderr, timed_out, _memory, _mechanism = spawn(
        list(command),
        cwd=str(cwd),
        env=env,
        read_paths=reads,
        workspace=cwd,
        cpu_seconds=max(1, int(timeout)),
        memory_bytes=memory_bytes(),
        network=False,
        timeout_seconds=float(timeout),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout)
    return _HelperResult(code, stdout or "", stderr or "")


# Locale and clock names a script needs in order to format text and time.
# Anything else in the parent environment stays there.
_SCRIPT_LOCALE = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_NUMERIC",
    "LC_TIME",
    "LC_COLLATE",
    "LC_MESSAGES",
    "LC_MONETARY",
    "TZ",
)

# The child applies these with the same caps the parent already read.
# An empty value is left unset so the child uses its default.
_SCRIPT_LIMITS = (
    "AICAD_SCRIPT_CPU_SECONDS",
    "AICAD_SCRIPT_MEMORY_MB",
    "AICAD_SCRIPT_FILE_BYTES",
)

# Text encoding for the script's own output. Copied only when the parent set one.
_SCRIPT_TEXT = ("PYTHONUTF8", "PYTHONIOENCODING")

# The confined process cannot read the account database, so Python resolves
# the home directory from this variable.
_SCRIPT_HOME = ("HOME",)

# Windows refuses to start a process whose environment omits these session
# names. They are the operating system's own names, copied only when set.
_WINDOWS_SESSION = (
    "ALLUSERSPROFILE",
    "APPDATA",
    "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)",
    "COMPUTERNAME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PUBLIC",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
)


def _copy_set(env: dict[str, str], names: tuple[str, ...]) -> None:
    for name in names:
        value = os.environ.get(name)
        if value:
            env[name] = value


def _child_env(
    workspace: Path,
    job: Path,
    network: bool,
    tmp: Path | None = None,
    cache: Path | None = None,
) -> dict[str, str]:
    """Environment for one script. Names that are not listed here are omitted.

    The script gets the system PATH, a trusted import path, the job
    variables, locale names, and the variables Windows needs in order to
    start. Temporary files stay in the project. A limit the parent set is
    passed through so this process applies that same limit.
    """

    env: dict[str, str] = {
        "PATH": _system_path(),
        # Do not prepend the working directory to sys.path. The working
        # directory is the project, and a module there would run before
        # the sandbox.
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "AICAD_SANDBOX_CHILD": "1",
        "AICAD_WORKSPACE": str(workspace),
        "AICAD_JOB": str(job),
        "AICAD_NETWORK": "1" if network else "0",
    }
    pythonpath = _trusted_pythonpath(workspace)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    _copy_set(env, _SCRIPT_LOCALE)
    _copy_set(env, _SCRIPT_TEXT)
    _copy_set(env, _SCRIPT_LIMITS)
    _copy_set(env, _SCRIPT_HOME)
    if sys.platform == "win32":
        # The network check reads the receivers the parent is listening on.
        # USERPROFILE is copied as the account profile. Pointing it at the
        # project cache made every script fail with STATUS_INVALID_HANDLE.
        _copy_set(env, ("AICAD_WIN_NET_RECEIVERS",))
        _copy_set(env, _WINDOWS_SESSION)
    _restore_os_environment(env)
    tmp_path = tmp if tmp is not None else workspace / ".aicad" / "tmp"
    tmp_text = str(tmp_path)
    env["TMPDIR"] = tmp_text
    env["TMP"] = tmp_text
    env["TEMP"] = tmp_text
    if sys.platform == "win32":
        # ezdxf and matplotlib otherwise stat the real user profile. An
        # AppContainer is denied that path.
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
    from aicad.paths import publish_text

    publish_text(secret, "aicad-confinement-probe\n", root=root)
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


def _terminate_windows(pid: int) -> None:
    """End one Windows process.

    ``SIGKILL`` is not termination there. ``os.kill`` asks for every process
    right, which an AppContainer part script refuses, so the process is
    opened with the terminate right only.
    """
    if pid <= 1 or pid == os.getpid() or sys.platform != "win32":
        return
    from aicad.sandbox_windows import terminate_pid

    terminate_pid(pid)


def _unlink_when_released(path: Path | None) -> None:
    """Remove ``path``. A file Windows still calls in use is tried again."""
    if path is None:
        return
    deadline = time.monotonic() + 5
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


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


def _descendants_from(leader: int, parent_of: dict[int, int]) -> set[int]:
    """Pids that descend from ``leader`` in ``parent_of``."""
    if leader <= 1:
        return set()
    children: dict[int, list[int]] = {}
    for pid, ppid in parent_of.items():
        if not isinstance(pid, int) or pid <= 1:
            continue
        children.setdefault(int(ppid), []).append(pid)
    found: set[int] = set()
    stack = list(children.get(leader, []))
    while stack:
        pid = stack.pop()
        if pid in found or pid <= 1 or pid == leader:
            continue
        found.add(pid)
        stack.extend(children.get(pid, []))
    return found


def _parent_map() -> dict[int, int]:
    """Pid to parent pid. Empty when this system cannot list processes."""
    parent_of: dict[int, int] = {}
    if sys.platform.startswith("linux"):
        try:
            names = os.listdir("/proc")
        except OSError:
            return {}
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
        return parent_of
    if sys.platform == "darwin":
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
            return {}
        for line in completed.stdout.splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                parent_of[int(parts[0])] = int(parts[1])
            except ValueError:
                continue
        return parent_of
    if sys.platform == "win32":
        from aicad.sandbox_windows import process_parents

        return process_parents()
    return {}


def _descendants(leader: int) -> set[int]:
    """Pids whose parent is ``leader`` or one of its descendants."""
    return _descendants_from(leader, _parent_map())


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


class _OutputCapture:
    """The tail of a process's pipes. A longer stream is discarded, not stored."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for pipe, buf in ((self.proc.stdout, self._stdout), (self.proc.stderr, self._stderr)):
            if pipe is None:
                continue
            thread = threading.Thread(
                target=self._drain,
                args=(pipe, buf),
                name="aicad-output",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _drain(self, pipe, buf: bytearray) -> None:
        try:
            while True:
                block = pipe.read(65536)
                if not block:
                    return
                buf.extend(block)
                extra = len(buf) - _SCRIPT_OUTPUT_CAP
                if extra > 0:
                    del buf[:extra]
        except (OSError, ValueError):
            return
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    def finish(self, timeout: float = 5) -> None:
        for thread in self._threads:
            thread.join(timeout=timeout)

    def stdout(self) -> bytes:
        return bytes(self._stdout)

    def stderr(self) -> bytes:
        return bytes(self._stderr)


class _ExitNote:
    """macOS notice that a process has exited, without collecting it.

    ``waitid`` can keep reporting that the process is still running after it
    has exited. libproc does not always call that process a zombie, and a
    zombie still answers ``kill``. Either one alone waits until the wall
    clock. This watch reports the exit on its own.
    """

    def __init__(self, pid: int) -> None:
        self._queue = None
        self._fired = False
        if sys.platform != "darwin" or pid <= 0:
            return
        import select

        if not hasattr(select, "kqueue"):
            return
        try:
            queue = select.kqueue()
            queue.control(
                [
                    select.kevent(
                        pid,
                        filter=select.KQ_FILTER_PROC,
                        flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                        fflags=select.KQ_NOTE_EXIT,
                    )
                ],
                0,
                0,
            )
        except OSError:
            return
        self._queue = queue

    def watching(self) -> bool:
        return self._queue is not None

    def fired(self) -> bool:
        if self._fired:
            return True
        queue = self._queue
        if queue is None:
            return False
        try:
            events = queue.control(None, 1, 0)
        except OSError:
            return False
        if events:
            self._fired = True
        return self._fired

    def close(self) -> None:
        queue = self._queue
        self._queue = None
        if queue is None:
            return
        try:
            queue.close()
        except OSError:
            pass


def _wait_script(
    proc: subprocess.Popen,
    wall: float,
    sweep,
    *,
    before_reap=None,
) -> tuple[bytes, bytes]:
    """Wait for ``proc``. On macOS, ``sweep`` runs while the script is still going.

    Standard output and standard error are drained while the process runs.
    Only the last ``_SCRIPT_OUTPUT_CAP`` bytes of each are kept.

    ``before_reap`` runs once the process has exited and before it is
    collected. The write counter is still available then. Collecting the
    process first drops it. A system that cannot report the exit without
    collecting the process is not collected until that reading has run.
    When no report is available at all, the process is collected so the
    wait cannot run out the wall clock.
    """
    deadline = time.monotonic() + wall
    capture = _OutputCapture(proc)
    capture.start()
    note = _ExitNote(proc.pid or 0)
    try:
        while True:
            unreaped = _exited_unreaped(proc.pid)
            ready = bool(unreaped) or note.fired()
            if not ready and unreaped is None:
                probed = _exited_for_reading(proc)
                if probed:
                    ready = True
                elif probed is None and not note.watching():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(proc.args, wall)
                    try:
                        proc.wait(timeout=min(0.05, remaining))
                    except subprocess.TimeoutExpired:
                        if sweep is not None:
                            sweep()
                        continue
                    break
            if ready:
                if before_reap is not None:
                    before_reap()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    continue
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, wall)
            if sweep is not None:
                sweep()
            time.sleep(min(0.02, remaining))
        capture.finish()
        return capture.stdout(), capture.stderr()
    except BaseException:
        capture.finish(timeout=0.2)
        raise
    finally:
        note.close()


def _exited_for_reading(proc: subprocess.Popen) -> bool | None:
    """Whether ``proc`` has exited, without collecting it.

    None means this system cannot tell a running process from a zombie.
    True means it is a zombie or already gone. The caller reads the write
    counter before collecting the process.
    """
    pid = proc.pid
    if pid is None or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except FileNotFoundError:
            return True
        except OSError:
            return None
        close = text.rfind(")")
        if close < 0 or close + 2 >= len(text):
            return None
        return text[close + 2] == "Z"
    if sys.platform == "darwin":
        from aicad.sandbox_macos import process_runtime

        runtime = process_runtime(pid)
        if runtime is not None:
            return bool(runtime[1])
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return True
            return None
        return None
    if sys.platform == "win32":
        handle = getattr(proc, "_handle", None)
        if not handle:
            return None
        from ctypes import wintypes

        code = wintypes.DWORD()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        # 259 is STILL_ACTIVE. Any other code means the process has exited
        # and has not been collected.
        return int(code.value) != 259
    return None


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


def _file_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _snapshot_files(root: Path) -> dict[str, int]:
    """Sizes of regular files under ``root``. A link is not followed."""
    from aicad.paths import is_reparse

    sizes: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept: list[str] = []
        for name in dirnames:
            child = Path(dirpath) / name
            if child.is_symlink() or is_reparse(child):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or is_reparse(path):
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            sizes[_file_key(path)] = int(info.st_size)
    return sizes


def _wchar_from_text(text: str) -> int | None:
    """The ``wchar`` total in a Linux ``/proc/<pid>/io`` snapshot."""
    for line in text.splitlines():
        if line.startswith("wchar:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _thread_group_leader(pid: int) -> int:
    """The thread-group id for ``pid``.

    ``/proc/<tgid>/task/<tid>/io`` is one thread. ``/proc/<tgid>/io`` is every
    thread in the process. The quota uses the group total, once.
    """
    if pid <= 0 or not sys.platform.startswith("linux"):
        return pid
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        return pid
    for line in text.splitlines():
        if not line.startswith("Tgid:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return pid
        try:
            tgid = int(parts[1])
        except ValueError:
            return pid
        return tgid if tgid > 0 else pid
    return pid


def _process_write_bytes(pid: int) -> int | None:
    """Logical bytes this process has written, or None when the system will not say.

    On Linux this is ``wchar`` for the whole thread group, the bytes passed to
    write. A file removed before the next directory look is still included.
    ``write_bytes`` is only what has reached storage, so it misses that file.
    Opening that file after the process has exited fails, so the caller keeps
    a descriptor from while the process was alive and reads it again at exit.
    """
    if sys.platform.startswith("linux"):
        leader = _thread_group_leader(pid)
        try:
            text = Path(f"/proc/{leader}/io").read_text(encoding="utf-8")
        except OSError:
            return None
        return _wchar_from_text(text)
    if sys.platform == "darwin":
        from aicad.sandbox_macos import bytes_written

        return bytes_written(pid)
    return None


def _release_write_fd(holder: list[int | None]) -> None:
    """Close the pipe that holds a script at startup.

    The script is waiting to read. Closing the write end makes that read
    return empty, which lets the script continue. Writing a byte is not
    used. A spawn that fails has no reader, and that write delivers
    SIGPIPE and stops this process where the signal is not ignored.
    """
    descriptor = holder[0]
    if descriptor is None:
        return
    holder[0] = None
    try:
        os.close(descriptor)
    except OSError:
        pass


def _exited_unreaped(pid: int) -> bool | None:
    """Whether ``pid`` has exited and has not been collected.

    None means this system cannot answer without collecting the process.
    A normal wait collects it, and the write counter is gone after that.
    """
    waitid = getattr(os, "waitid", None)
    exited = getattr(os, "WEXITED", None)
    nohang = getattr(os, "WNOHANG", None)
    nowait = getattr(os, "WNOWAIT", None)
    if waitid is None or None in (exited, nohang, nowait):
        return None
    try:
        status = waitid(os.P_PID, pid, exited | nohang | nowait)
    except (AttributeError, ChildProcessError, OSError):
        return None
    return status is not None


def _open_real_directory(path: Path) -> int | None:
    """Open one real directory. A symlink or junction is not opened.

    The quota watch uses this so a link cannot move the directory it counts.
    """

    from aicad.paths import is_reparse

    try:
        if path.is_symlink() or is_reparse(path) or not path.is_dir():
            return None
    except OSError:
        return None
    flags = os.O_RDONLY
    for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"):
        flag = getattr(os, name, 0)
        if flag:
            flags |= flag
    try:
        return os.open(os.fspath(path), flags)
    except OSError:
        return None


class _DiskWatch:
    """Cumulative and per-file write caps for one script.

    The parent counts bytes written and the files that grew. A run past either
    cap is stopped, and the extra bytes are removed afterwards.
    """

    # How long to wait for the watch to leave the tree. A longer read is
    # treated as a failed quota check instead of holding the run.
    _stop_timeout = 2.0

    def __init__(self, root: Path, before: dict[str, int], file_cap: int, disk_cap: int):
        self.root = root
        self.before = before
        self.file_cap = file_cap
        self.disk_cap = disk_cap
        self.hit = threading.Event()
        self._directories = _snapshot_directories(root)
        self._io_base: dict[int, int] = {}
        self._io_fds: dict[int, int] = {}
        self._pids: set[int] = set()
        self._file_peak: dict[str, int] = {}
        self._known_names: dict[str, set[str]] = {}
        self._held: dict[str, int] = {}
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._event_thread: threading.Thread | None = None
        self._watch_ok = False
        self._wake_write: int | None = None
        self._windows_directory = None
        self._folded: set[int] = set()
        self._child_extra = 0
        self.child_report: Path | None = None
        self.io_peak = 0
        self._lock = threading.Lock()

    def hold_counter(self, pid: int) -> None:
        """Keep this process's write counter readable after it exits.

        On Linux, ``/proc/<pid>/io`` cannot be opened for a zombie, and it
        disappears once the process is collected. A descriptor opened while
        the process is alive still returns ``wchar`` until then.
        """
        if pid <= 0 or not sys.platform.startswith("linux"):
            return
        pid = _thread_group_leader(pid)
        with self._lock:
            if pid in self._io_fds:
                return
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(f"/proc/{pid}/io", flags)
        except OSError:
            return
        with self._lock:
            if pid in self._io_fds:
                os.close(descriptor)
                return
            self._io_fds[pid] = descriptor

    def close_counters(self) -> bool:
        """Stop the watch. True when it has left the tree.

        The watch is woken, then given ``_stop_timeout`` seconds. If it is
        still inside a read, the quota is exceeded and the held files stay
        with that thread. The caller must not remove the tree until the
        thread returns.
        """

        self._wake_watch()
        thread = self._event_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._stop_timeout)
            if thread.is_alive():
                self.hit.set()
                return False
        self._release_held_files()
        return True

    def _wake_watch(self) -> None:
        """Ask the watch to leave, and wake a read that is waiting."""

        self._stop.set()
        with self._lock:
            wake = self._wake_write
            if wake is not None:
                try:
                    os.write(wake, b"\0")
                except OSError:
                    pass
            handle = self._windows_directory
            if handle is not None and sys.platform == "win32":
                from aicad.sandbox_windows import cancel_directory_watch

                cancel_directory_watch(handle)

    def _remember_windows_directory(self, handle) -> None:
        with self._lock:
            self._windows_directory = handle

    def _release_held_files(self) -> None:
        with self._lock:
            held = list(self._held.items())
            self._held.clear()
            descriptors = list(self._io_fds.values())
            self._io_fds.clear()
        for key, descriptor in held:
            try:
                info = os.fstat(descriptor)
            except OSError:
                info = None
            if info is not None and stat.S_ISREG(info.st_mode):
                self.note_size(key, int(info.st_size))
            try:
                os.close(descriptor)
            except OSError:
                pass
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def note_size(self, key: str, size: int) -> None:
        """Remember ``size`` for ``key`` even if that file is gone later."""
        if isinstance(size, bool):
            return
        try:
            size = int(size)
        except (TypeError, ValueError):
            return
        if size < 0:
            return
        with self._lock:
            old = self.before.get(key, 0)
            prior = self._file_peak.get(key, old)
            if size > prior:
                self._file_peak[key] = size
            peaks = dict(self._file_peak)
            before = self.before
            file_cap = self.file_cap
            disk_cap = self.disk_cap
        charged = max(peaks.get(key, 0), size)
        if charged > file_cap and charged > old:
            self.hit.set()
            return
        total = 0
        for item in set(before) | set(peaks):
            previous = before.get(item, 0)
            total += max(0, max(peaks.get(item, 0), previous) - previous)
            if total > disk_cap:
                self.hit.set()
                return

    def retain(self, path: Path) -> None:
        """Open ``path`` and keep its size after the name is removed.

        The directory change is what finds the name. The descriptor is what
        still has the size once that name is gone.
        """
        from aicad.paths import is_reparse

        try:
            if path.is_symlink() or is_reparse(path) or not _inside(path, self.root):
                return
        except OSError:
            return
        key = _file_key(path)
        if sys.platform == "win32":
            from aicad.sandbox_windows import named_size, shared_read

            with self._lock:
                held = self._held.get(key)
            if held is not None:
                self._note_descriptor(key, held)
                return
            descriptor = shared_read(path)
            if descriptor is None:
                size = named_size(path)
                if size is not None:
                    self.note_size(key, size)
                return
            try:
                info = os.fstat(descriptor)
            except OSError:
                os.close(descriptor)
                return
            self.note_size(key, int(info.st_size))
            with self._lock:
                current = self._held.get(key)
                if current is not None or len(self._held) >= 256:
                    drop = descriptor
                else:
                    self._held[key] = descriptor
                    drop = None
            if drop is not None:
                os.close(drop)
            return
        with self._lock:
            held = self._held.get(key)
        if held is not None:
            self._note_descriptor(key, held)
            return
        descriptor = _open_retained(path)
        if descriptor is None:
            self._note_linked(path)
            return
        try:
            info = os.fstat(descriptor)
        except OSError:
            os.close(descriptor)
            return
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            return
        self.note_size(key, int(info.st_size))
        with self._lock:
            current = self._held.get(key)
            if current is not None or len(self._held) >= 256:
                drop = descriptor
            else:
                self._held[key] = descriptor
                drop = None
        if drop is not None:
            os.close(drop)

    def release(self, path: Path) -> None:
        """Read a retained file one last time and close it."""
        key = _file_key(path)
        with self._lock:
            descriptor = self._held.pop(key, None)
        if descriptor is None:
            return
        self._note_descriptor(key, descriptor)
        try:
            os.close(descriptor)
        except OSError:
            pass

    def _note_linked(self, path: Path) -> None:
        try:
            info = path.lstat()
        except OSError:
            return
        if stat.S_ISREG(info.st_mode):
            self.note_size(_file_key(path), int(info.st_size))

    def _note_descriptor(self, key: str, descriptor: int) -> None:
        try:
            info = os.fstat(descriptor)
        except OSError:
            return
        if stat.S_ISREG(info.st_mode):
            self.note_size(key, int(info.st_size))

    def scan_new_files(self) -> None:
        """Open files that appeared since the last look.

        The first look only records names. A file that is created and removed
        before a later look is missed, so callers repeat this often.
        """
        known = self._known_names
        pending = [str(self.root)]
        visited: set[str] = set()
        while pending:
            if self._stop.is_set():
                return
            directory = pending.pop()
            if directory in visited:
                continue
            visited.add(directory)
            previous = known.get(directory)
            live: set[str] = set()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            from aicad.paths import is_reparse

            for entry in entries:
                try:
                    child = Path(entry.path)
                    if entry.is_symlink() or is_reparse(child):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        live.add(entry.path)
                        if previous is not None and entry.path not in previous:
                            self.retain(Path(entry.path))
                except OSError:
                    continue
            if previous is not None:
                for gone in previous - live:
                    self.release(Path(gone))
            known[directory] = live
        self._reread_held()

    def _reread_held(self) -> None:
        with self._lock:
            held = list(self._held.items())
        for key, descriptor in held:
            self._note_descriptor(key, descriptor)

    def arm(self) -> bool:
        """Watch the project directory until :meth:`close_counters`.

        False when this system could not install the watch. A timer sample
        still runs in that case.
        """
        if self._event_thread is not None:
            self._ready.wait(timeout=2)
            return self._watch_ok
        thread = threading.Thread(target=self._event_main, name="aicad-files", daemon=True)
        self._event_thread = thread
        thread.start()
        self._ready.wait(timeout=2)
        return self._watch_ok

    def _mark_watch(self, ok: bool) -> None:
        self._watch_ok = ok
        self._ready.set()

    def _event_main(self) -> None:
        try:
            if sys.platform.startswith("linux"):
                self._linux_directory_watch()
            elif sys.platform == "darwin":
                self.scan_new_files()
                self._mark_watch(True)
                while not self._stop.is_set():
                    try:
                        self.scan_new_files()
                    except OSError:
                        pass
                    if self._stop.wait(0.001):
                        break
            elif sys.platform == "win32":
                from aicad.sandbox_windows import run_directory_watch

                run_directory_watch(
                    self.root,
                    self.retain,
                    self.release,
                    self._reread_held,
                    self.note_files,
                    self._stop,
                    self._mark_watch,
                    self._remember_windows_directory,
                )
            else:
                self._mark_watch(False)
        except Exception:
            self._mark_watch(False)
        if self._watch_ok:
            return
        while not self._stop.is_set():
            try:
                self.note_files()
            except OSError:
                pass
            if self.hit.is_set():
                return
            self._stop.wait(0.02)

    def _linux_directory_watch(self) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.inotify_init1.argtypes = [ctypes.c_int]
        libc.inotify_init1.restype = ctypes.c_int
        libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        libc.inotify_add_watch.restype = ctypes.c_int
        init_flags = 0x00080000 | 0x00000800  # IN_CLOEXEC | IN_NONBLOCK
        descriptor = int(libc.inotify_init1(init_flags))
        if descriptor < 0:
            self._mark_watch(False)
            return
        # CREATE through DELETE, plus a move into or out of the directory.
        mask = 0x00000002 | 0x00000008 | 0x00000040 | 0x00000080 | 0x00000100 | 0x00000200
        watches: dict[int, Path] = {}

        def add_watch(directory: Path) -> None:
            opened = _open_real_directory(directory)
            if opened is None:
                return
            try:
                # The descriptor is the directory that was opened. Watching
                # that descriptor does not follow a link put at the path later.
                watched = f"/proc/self/fd/{opened}"
                watch = int(libc.inotify_add_watch(descriptor, os.fsencode(watched), mask | 0x01000000))
            finally:
                os.close(opened)
            if watch >= 0:
                watches[watch] = directory

        try:
            from aicad.paths import is_reparse

            for dirpath, dirnames, _names in os.walk(self.root, followlinks=False):
                if self._stop.is_set():
                    break
                current = Path(dirpath)
                if current.is_symlink() or is_reparse(current):
                    dirnames.clear()
                    continue
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not (current / name).is_symlink() and not is_reparse(current / name)
                ]
                add_watch(current)
        except OSError:
            os.close(descriptor)
            self._mark_watch(False)
            return
        if not watches:
            os.close(descriptor)
            self._mark_watch(False)
            return
        wake_flags = getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        if hasattr(os, "pipe2") and wake_flags:
            wake_read, wake_write = os.pipe2(wake_flags)
        else:
            wake_read, wake_write = os.pipe()
        with self._lock:
            self._wake_write = wake_write
        self._mark_watch(True)
        try:
            while not self._stop.is_set():
                try:
                    readable, _, _ = select.select([descriptor, wake_read], [], [], 0.02)
                except InterruptedError:
                    continue
                if descriptor in readable:
                    self._read_inotify(descriptor, watches, add_watch)
                self._reread_held()
                if self._stop.is_set() or wake_read in readable:
                    break
        finally:
            with self._lock:
                self._wake_write = None
                os.close(wake_write)
            os.close(wake_read)
            os.close(descriptor)

    def _read_inotify(self, descriptor: int, watches: dict[int, Path], add_watch) -> None:
        try:
            data = os.read(descriptor, 65536)
        except OSError:
            return
        offset = 0
        while offset + 16 <= len(data):
            watch = int.from_bytes(data[offset : offset + 4], "little", signed=True)
            event_mask = int.from_bytes(data[offset + 4 : offset + 8], "little")
            name_len = int.from_bytes(data[offset + 12 : offset + 16], "little")
            raw = data[offset + 16 : offset + 16 + name_len].split(b"\x00", 1)[0]
            offset += 16 + name_len
            if event_mask & 0x00004000:  # overflow: look at the tree directly
                self.note_files()
                continue
            if event_mask & 0x00008000:
                watches.pop(watch, None)
                continue
            directory = watches.get(watch)
            if directory is None:
                continue
            if not raw:
                self.note_files()
                continue
            path = directory / os.fsdecode(raw)
            if event_mask & 0x40000000 and event_mask & 0x00000100:
                add_watch(path)
                continue
            if event_mask & (0x00000200 | 0x00000040):
                self.release(path)
                continue
            if event_mask & (0x00000100 | 0x00000002 | 0x00000008 | 0x00000080):
                self.retain(path)

    def _counter(self, pid: int) -> int | None:
        with self._lock:
            descriptor = self._io_fds.get(pid)
            if descriptor is not None:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    data = os.read(descriptor, 4096)
                except OSError:
                    data = b""
                if data:
                    value = _wchar_from_text(data.decode("utf-8", "replace"))
                    if value is not None:
                        return value
        return _process_write_bytes(pid)

    def baseline(self, pid: int) -> None:
        """Remember bytes already written, so only later writes count.

        A process that was not seen here counts from zero on the first sample.
        That sample is not itself a baseline.
        """
        pid = _thread_group_leader(pid)
        current = self._counter(pid)
        with self._lock:
            if pid not in self._io_base:
                self._io_base[pid] = 0 if current is None else current

    def note_io(self, pids: set[int]) -> None:
        leaders: list[int] = []
        seen: set[int] = set()
        for pid in pids:
            leader = _thread_group_leader(pid)
            if leader in seen:
                continue
            seen.add(leader)
            leaders.append(leader)
        with self._lock:
            self._pids = set(pids)
            folded = set(self._folded)
        total = 0
        live: dict[int, int] = {}
        for pid in leaders:
            if pid in folded:
                continue
            current = self._counter(pid)
            if current is None:
                continue
            with self._lock:
                if pid in self._folded:
                    continue
                base = self._io_base.get(pid)
                if base is None:
                    base = 0
                    self._io_base[pid] = 0
            delta = max(0, current - base)
            live[pid] = delta
            total += delta
        with self._lock:
            # A child collected while this sample was reading is owned by the
            # report. Drop the live delta so that child is not added twice.
            for pid, delta in live.items():
                if pid in self._folded:
                    total -= delta
            total += self._child_extra
            self.io_peak = max(self.io_peak, total)
        if total > self.disk_cap:
            self.hit.set()

    def account_io(self, pids: set[int]) -> None:
        """Read a child report, then the live counters, as one sample.

        A process in ``pids`` is counted from its own counter. The report
        cannot replace that total.
        """
        protected: set[int] = set()
        for pid in pids:
            if isinstance(pid, int) and pid > 0:
                protected.add(pid)
                protected.add(_thread_group_leader(pid))
        self.absorb_child_report(protected)
        self.note_io(pids)

    def note_reported_children(self, amounts: dict[int, int]) -> None:
        """Add logical writes of children that were collected before a counter was opened.

        ``amounts`` maps a pid to the bytes that process had written. A pid
        already included here is left out of later live samples so it is not
        counted twice.
        """
        extra = 0
        fresh: set[int] = set()
        for pid, total in amounts.items():
            if not isinstance(pid, int) or pid <= 0:
                continue
            with self._lock:
                if pid in self._folded:
                    continue
                base = self._io_base.get(pid, 0)
            try:
                amount = int(total)
            except (TypeError, ValueError):
                continue
            if amount < 0:
                continue
            extra += max(0, amount - base)
            fresh.add(pid)
        if not fresh:
            return
        with self._lock:
            self._folded.update(fresh)
            self._child_extra += extra
            self.io_peak = max(self.io_peak, self._child_extra)
        if self.io_peak > self.disk_cap:
            self.hit.set()

    def absorb_child_report(self, protected: set[int] | None = None) -> None:
        """Read collected-child writes. A live process is left on its own counter.

        The report lives in the project. It can add a child that was collected
        before a counter was opened. It cannot change the total for a process
        the watch is already counting, and it cannot move the watched directory.
        """
        path = self.child_report
        if path is None:
            return
        from aicad.file_access import read_text

        try:
            text = read_text(path, maximum=1_000_000)
        except OSError:
            return
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        skip = protected or set()
        amounts: dict[int, int] = {}
        for key, value in payload.items():
            try:
                pid = int(key)
                amount = int(value)
            except (TypeError, ValueError):
                continue
            if pid <= 0 or amount < 0:
                continue
            if pid in skip or _thread_group_leader(pid) in skip:
                continue
            amounts[pid] = amount
        self.note_reported_children(amounts)

    def note_job_io(self, written: int, baseline: int | None) -> None:
        delta = written if baseline is None else max(0, written - baseline)
        with self._lock:
            self.io_peak = max(self.io_peak, delta)
        if delta > self.disk_cap:
            self.hit.set()

    def note_files(self, pids: set[int] | None = None) -> None:
        self._reread_held()
        if pids is not None:
            with self._lock:
                self._pids = set(pids)
        try:
            over = self._growth_over()
        except OSError:
            return
        if over:
            self.hit.set()

    def _accounted_rows(self) -> list[tuple[str, int, int]]:
        """``(key, charged_size, old_size)`` for every file this run has seen.

        ``charged_size`` is the largest size observed, not the size now. A file
        removed before the next snapshot still counts at that size. On Linux an
        open file that has already been unlinked is included too.
        """
        current = _snapshot_files(self.root)
        with self._lock:
            pids = set(self._pids)
        for pid in pids:
            for key, size in _open_file_sizes(pid, self.root).items():
                if size > current.get(key, 0):
                    current[key] = size
        with self._lock:
            for key, size in current.items():
                prior = self._file_peak.get(key, self.before.get(key, 0))
                if size > prior:
                    self._file_peak[key] = size
            peaks = dict(self._file_peak)
        rows: list[tuple[str, int, int]] = []
        for key in set(self.before) | set(current) | set(peaks):
            old = self.before.get(key, 0)
            charged = max(peaks.get(key, current.get(key, 0)), current.get(key, 0))
            if charged == 0 and old == 0:
                continue
            rows.append((key, charged, old))
        return rows

    def _growth_over(self) -> bool:
        total = 0
        for _key, size, old in self._accounted_rows():
            if size > self.file_cap and size > old:
                return True
            total += max(0, size - old)
            if total > self.disk_cap:
                return True
        return False

    def settle(self) -> str | None:
        """Remove writes past the caps. The message is None when the run stayed inside them."""
        if sys.platform == "win32":
            from aicad.sandbox_windows import _reclaim_for_user

            _reclaim_for_user(self.root)
        grown: list[tuple[int, str]] = []
        total = 0
        file_over = False
        for key, size, old in self._accounted_rows():
            growth = max(0, size - old)
            if size > self.file_cap and size > old:
                file_over = True
                _restore_file(key, self.before)
                continue
            if growth:
                grown.append((growth, key))
                total += growth
        with self._lock:
            io_peak = self.io_peak
        used = max(total, io_peak)
        if used > self.disk_cap:
            grown.sort(reverse=True)
            for growth, key in grown:
                if total <= self.disk_cap and io_peak <= self.disk_cap:
                    break
                _restore_file(key, self.before)
                total -= growth
            _remove_new_empty_directories(self.root, self._directories)
            return f"The script was stopped because it wrote more than {self.disk_cap} bytes."
        if file_over:
            _remove_new_empty_directories(self.root, self._directories)
            return f"The script was stopped because it wrote more than {self.file_cap} bytes."
        return None


def _snapshot_directories(root: Path) -> set[str]:
    """Directories under ``root``. A link is not entered."""
    from aicad.paths import is_reparse

    found: set[str] = set()
    for dirpath, dirnames, _names in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if child.is_symlink() or is_reparse(child):
                continue
            kept.append(name)
            found.add(_file_key(child))
        dirnames[:] = kept
    return found


def _remove_new_empty_directories(root: Path, before: set[str]) -> None:
    """Remove empty directories created during a run that passed its quota.

    A stopped script may have created a folder and not removed it. A folder
    that still has a file, or that was already there, stays.
    """
    from aicad.file_access import remove_empty_directory
    from aicad.paths import is_reparse

    found: list[Path] = []
    for dirpath, dirnames, _names in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if child.is_symlink() or is_reparse(child):
                continue
            kept.append(name)
            if _file_key(child) not in before:
                found.append(child)
        dirnames[:] = kept
    for child in sorted(found, key=lambda path: len(path.parts), reverse=True):
        try:
            if child.is_symlink() or is_reparse(child) or any(child.iterdir()):
                continue
            remove_empty_directory(child)
        except OSError:
            continue


def _open_retained(path: Path) -> int | None:
    """A read-only descriptor that does not follow a link, or None."""
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flag = getattr(os, name, 0)
        if flag:
            flags |= flag
    try:
        return os.open(os.fspath(path), flags)
    except OSError:
        return None


def _open_file_sizes(pid: int, root: Path) -> dict[str, int]:
    """Sizes of regular files ``pid`` still has open under ``root``.

    An unlinked file stays visible here until the descriptor is closed. The
    directory snapshot does not see it. Linux reads ``/proc/<pid>/fd``. macOS
    reads the same fact from libproc.
    """
    if pid <= 0:
        return {}
    if sys.platform == "darwin":
        from aicad.sandbox_macos import open_file_sizes

        return open_file_sizes(pid, root)
    if not sys.platform.startswith("linux"):
        return {}
    root_resolved = root.resolve()
    sizes: dict[str, int] = {}
    try:
        names = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return {}
    for name in names:
        link = f"/proc/{pid}/fd/{name}"
        try:
            target = os.readlink(link)
            info = os.stat(link)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        if target.endswith(" (deleted)"):
            target = target[: -len(" (deleted)")]
        if not target.startswith("/"):
            continue
        path = Path(target)
        if not _inside(path, root_resolved):
            continue
        key = _file_key(path)
        size = int(info.st_size)
        if size > sizes.get(key, 0):
            sizes[key] = size
    return sizes


def _file_growth(root: Path, before: dict[str, int]):
    """Yield ``(key, size, old_size)`` for regular files. ``old_size`` is 0 for a new file."""
    current = _snapshot_files(root)
    keys = set(before) | set(current)
    for key in keys:
        size = current.get(key, 0)
        old = before.get(key, 0)
        if size == 0 and old == 0:
            continue
        yield key, size, old


def _restore_file(key: str, before: dict[str, int]) -> None:
    from aicad.file_access import unlink_name

    path = Path(key)
    old = before.get(key)
    try:
        if old is None:
            try:
                unlink_name(path)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise
            return
        os.truncate(path, old)
    except OSError:
        try:
            unlink_name(path)
        except OSError:
            return


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
    disk: _DiskWatch | None = None,
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
        if disk is not None:
            watched = {leader, *found}
            for pid in watched:
                disk.hold_counter(pid)
            disk.account_io(watched)
            disk.note_files()
            if disk.hit.is_set():
                _kill_process_tree(leader, seen, lock, extra=found)
                return
        if time.monotonic() >= next_sample:
            next_sample = time.monotonic() + _MEMORY_POLL_SECONDS
            if memory_cap is not None:
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


def _stop_child(
    proc: subprocess.Popen,
    seen: set[int],
    seen_lock: threading.Lock,
    *,
    reap_seconds: float = 5,
    before_reap=None,
) -> None:
    """Kill a sandbox child and do not wait forever for it to disappear.

    The first signal is the process group. If the process is still alive, it
    is signalled again and the pipes are closed. The waits are bounded, so a
    child that does not die cannot hold the test until the job is cancelled.
    ``before_reap`` runs after the signal and before the process is collected,
    while its write counter can still be read.
    """
    _kill_process_tree(proc.pid, seen, seen_lock)
    # Close the pipes instead of reading them. A long stream would be stored
    # by communicate(), and the child can exit once the write end is gone.
    _close_child_pipes(proc)
    if before_reap is not None:
        before_reap()
    try:
        proc.wait(timeout=reap_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    _kill_process_tree(proc.pid, seen, seen_lock)
    if before_reap is not None:
        before_reap()
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


def _write_new_temp(prefix: str, suffix: str, data: bytes, directory: str | None = None) -> Path:
    """A new temporary file, written on the descriptor that created it.

    The temporary directory may itself be a link. The bytes go to the file
    this call created, so that link is not opened again to write them.
    """

    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=directory)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    except OSError:
        os.close(descriptor)
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    os.close(descriptor)
    return Path(name)


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
        self._directory: Path | None = None
        self._device = None
        self._inode = None

    def start(self) -> None:
        calls = self.job / "calls"
        calls.mkdir(mode=0o700)
        directory = _control_directory()
        self._directory = directory
        try:
            descriptor, name = tempfile.mkstemp(
                prefix="aicad-design-checks-",
                suffix=".json",
                dir=directory,
            )
        except OSError:
            from aicad.paths import remove_tree

            remove_tree(directory, ignore_errors=True)
            self._directory = None
            raise
        try:
            self._start_record_process(descriptor, name)
        finally:
            os.close(descriptor)
            self._record_fd = None

    def _start_record_process(self, descriptor: int, name: str) -> None:
        os.set_inheritable(descriptor, True)
        self.record = Path(name)
        info = os.fstat(descriptor)
        self._device = info.st_dev
        self._inode = info.st_ino
        self._record_fd = descriptor
        from aicad.file_access import create_file

        log_path = Path(name + ".log")
        log_fd = create_file(log_path.parent, log_path.name)
        self._log = log_path
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

        command = [
            sys.executable,
            "-P",
            "-B",
            "-u",
            str(_sandbox_runner()),
            "--model",
            str(self.job),
            str(self.record),
        ]
        popen_kwargs: dict = {
            "cwd": str(self.workspace),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": log_fd,
            "stderr": subprocess.STDOUT,
            "start_new_session": True,
            "close_fds": True,
        }
        if sys.platform == "win32":
            import msvcrt

            handle = int(msvcrt.get_osfhandle(descriptor))
            command.append(str(handle))
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [handle]}
            popen_kwargs["startupinfo"] = startup
        else:
            command.append(str(descriptor))
            popen_kwargs["pass_fds"] = (descriptor,)
        try:
            with child_inherits_geometry_stack():
                self.proc = subprocess.Popen(command, **popen_kwargs)
        finally:
            os.close(log_fd)

    def failure_detail(self) -> str:
        proc = self.proc
        if proc is None or proc.poll() in (0, None) or self._log is None:
            return ""
        try:
            text = _read_log_tail(self._log, 500).strip()
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
        from aicad.file_access import create_file

        try:
            descriptor = create_file(stop.parent, stop.name)
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
            self.proc.communicate(timeout=wall_seconds(self.workspace))
        except subprocess.TimeoutExpired:
            # Stop part scripts before the model. Killing the model first
            # reparents them, and on Windows they still hold the check record.
            self._stop_model()
            self._refuse()
        if self.proc.returncode != 0:
            self._refuse()
        from aicad.file_access import open_read

        try:
            descriptor = open_read(self.record)
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

    def _stop_model(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            children = _descendants(proc.pid)
        except OSError:
            children = set()
        if sys.platform == "win32":
            # ``SIGKILL`` does not terminate a Windows process. A part script
            # the model started is stopped first, while the model is still
            # its parent, so the check record is not left open.
            for pid in children:
                _terminate_windows(pid)
            _terminate_windows(proc.pid)
        else:
            _kill_process_tree(proc.pid, children, threading.Lock())
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def close(self) -> None:
        """Stop the model process, then remove the record it had open.

        The record is created without sharing delete, so Windows refuses to
        remove it while that process, or a child that inherited the handle,
        still has it. The wait is bounded. A sharing error after the process
        has exited is the handle closing, and it is tried again. The record
        and its log live in a private directory outside the project.
        """

        self._stop_model()
        directory = self._directory
        record = self.record
        log = self._log
        self.record = None
        self._log = None
        self._directory = None
        _unlink_when_released(record)
        _unlink_when_released(log)
        if directory is not None:
            from aicad.paths import remove_tree

            remove_tree(directory, ignore_errors=True)


def _control_directory() -> Path:
    """A new folder outside every project.

    A check record and a confinement profile are written here. The process
    temporary directory is not used when that directory is inside a project.
    """

    from aicad.paths import _make_private_tool_directory

    return _make_private_tool_directory()


def _finish_script_disk(disk: _DiskWatch, job: Path, *, keep_job: bool) -> bool:
    """Stop the quota watch, then remove ``job`` only after the watch has left.

    False means the watch was still in the tree when the wait ended. The
    quota is exceeded, and ``job`` is removed only once that thread returns.
    """

    released = disk.close_counters()
    if released:
        if not keep_job:
            shutil.rmtree(job, ignore_errors=True)
        return True
    disk.hit.set()
    threading.Thread(
        target=_remove_job_when_watch_leaves,
        args=(disk, job),
        name="aicad-job-cleanup",
        daemon=True,
    ).start()
    return False


def _remove_job_when_watch_leaves(disk: _DiskWatch, job: Path) -> None:
    thread = disk._event_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join()
    disk._release_held_files()
    shutil.rmtree(job, ignore_errors=True)


def _spawn(workspace: Path, kind: str, script: str | None, network: bool, helper: bool = False) -> dict:
    ok, reason = sandbox_supported()
    if not ok:
        raise ScriptError(reason)
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    _refuse_multilink(workspace)
    from aicad.paths import is_reparse, output_dir, publish_text

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
        if helper:
            spec["helper_read"] = spec["probe_read"]
    publish_text(job / "job.json", json.dumps(spec) + "\n", root=job)
    if script is not None:
        publish_text(job / "script.py", script, root=job)
    # -P is the same rule as PYTHONSAFEPATH: the project is the working
    # directory, and it must not be the first place Python imports from.
    # The runner is this install's file, not a module name looked up from
    # the working directory.
    command = _child_command(job)
    profile_path: Path | None = None
    profile_dir: Path | None = None
    if sys.platform == "darwin":
        from aicad.sandbox_macos import build_profile, command_prefix

        profile = build_profile(
            [str(path) for path in _read_roots()],
            [str(workspace)],
            network=network,
        )
        profile_dir = _control_directory()
        try:
            # Write on the descriptor that created the file. Passing the
            # private directory keeps that file out of a project temporary
            # directory, including when the temporary directory is a link.
            profile_path = _write_new_temp(
                "aicad-seatbelt-",
                ".sb",
                profile.encode("utf-8"),
                directory=str(profile_dir),
            )
        except Exception:
            from aicad.paths import remove_tree

            remove_tree(profile_dir, ignore_errors=True)
            profile_dir = None
            raise
        command = [*command_prefix(str(profile_path), workspace), *command]
    wall = wall_seconds(workspace)
    disk = _DiskWatch(workspace, _snapshot_files(workspace), file_bytes(), disk_bytes())
    disk.child_report = job / "child-writes.json"
    quota_hold: list[int | None] = [None]
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
            # The watch is in place before the script runs. Windows starts the
            # process inside this call, so the watch has to exist already.
            disk.arm()
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
                    disk=disk,
                )
            if timed_out:
                detail = design_record.failure_detail() if design_record is not None else ""
                # The job folder is removed after the watch releases the tree.
                message = f"The script ran longer than {wall} seconds, so it was stopped."
                if detail:
                    message = f"{message}\n{detail}"
                raise ScriptError(message)
            if design_record is not None and design_record.proc is not None and design_record.proc.poll() not in (0, None):
                design_record._refuse()
            disk_message = disk.settle()
            if disk_message:
                # The job folder is removed after the watch releases the tree.
                raise ScriptError(disk_message)
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

        # The script waits on this pipe. The counter is opened and baselined
        # before the write end is closed. Closing it lets the script continue,
        # so a run that writes and exits is still counted.
        hold_read, hold_write = os.pipe()
        quota_hold[0] = hold_write
        try:
            os.set_inheritable(hold_read, True)
            env["AICAD_QUOTA_HOLD"] = str(hold_read)
            inherited = tuple(popen_kwargs.get("pass_fds", ()))
            popen_kwargs["pass_fds"] = (*inherited, hold_read)
            popen_kwargs["close_fds"] = True
            with child_inherits_geometry_stack():
                proc = subprocess.Popen(command, **popen_kwargs)
        finally:
            os.close(hold_read)
        # macOS lists files from the sampler thread that already exists.
        # A second watch thread in this process stops the parent while a script runs.
        if sys.platform == "darwin":
            disk.scan_new_files()
        else:
            disk.arm()
        disk.hold_counter(proc.pid)
        disk.baseline(proc.pid)

        def _read_disk_at_exit() -> None:
            with seen_lock:
                pids = set(seen)
            pids.add(proc.pid)
            try:
                pids.update(_descendants(proc.pid))
            except OSError:
                pass
            for pid in pids:
                disk.hold_counter(pid)
            disk.account_io(pids)

        if account is not None and sentinel_path is not None:
            identity = process_identity(proc.pid)
            if identity is not None:
                account.add_ids({identity[0]})
        if pids_cgroup is not None:
            try:
                _require_pids_cgroup(pids_cgroup, proc.pid)
            except ScriptError:
                _stop_child(proc, seen, seen_lock, before_reap=_read_disk_at_exit)
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
            kwargs={"account": account, "sentinel": sentinel_path, "disk": disk},
            name="aicad-sandbox-reap",
            daemon=True,
        )
        watcher.start()
        # The script has been waiting on the quota pipe. Release it only after
        # the file sampler is running.
        _release_write_fd(quota_hold)

        def sweep() -> None:
            if account is None or sentinel_path is None:
                return
            _sweep_escaped(proc.pid, sentinel_path, account, seen, seen_lock)

        try:
            stdout_b, stderr_b = _wait_script(
                proc,
                wall,
                sweep if account is not None else None,
                before_reap=_read_disk_at_exit,
            )
        except subprocess.TimeoutExpired:
            if account is not None and not _join_reaper(stop_watch, watcher):
                account.fail()
            _stop_child(proc, seen, seen_lock, before_reap=_read_disk_at_exit)
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
        disk_message = disk.settle()
        if disk_message:
            shutil.rmtree(job, ignore_errors=True)
            raise ScriptError(disk_message)
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
        _release_write_fd(quota_hold)
        if stop_watch is not None:
            stop_watch.set()
        if watcher is not None:
            watcher.join(timeout=1)
        released = _finish_script_disk(disk, job, keep_job=keep_job)
        _release_pids_cgroup(pids_cgroup)
        if profile_dir is not None:
            from aicad.paths import remove_tree

            remove_tree(profile_dir, ignore_errors=True)
        if sentinel_fd is not None and sentinel_path is not None:
            from aicad.sandbox_macos import close_sentinel

            close_sentinel(sentinel_fd, sentinel_path)
        if probe_root is not None:
            shutil.rmtree(probe_root, ignore_errors=True)
        if design_record is not None:
            design_record.close()
        if not released and sys.exc_info()[0] is None:
            raise ScriptError("The script was stopped because the disk quota watch did not finish.")


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
    another name raises ``ScriptError`` before a byte of that file is returned.
    """
    from aicad.file_access import read_from_root
    from aicad.paths import _TooLarge, _before_nofollow

    parent = path.parent
    relative = Path(path.name)
    _before_nofollow(parent, relative)
    try:
        data = read_from_root(parent, relative, 512 * 1024 * 1024)
    except _TooLarge as exc:
        raise ScriptError("The script result could not be read.") from exc
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        if exc.errno == errno.EINVAL and exc.strerror == "not an ordinary file":
            return None
        if exc.errno in {errno.ELOOP, errno.ENOTDIR} or exc.strerror in {"is a link", "has another name"}:
            raise ScriptError("The script result is a link and was not read.") from exc
        raise ScriptError("The script result could not be read.") from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScriptError("The script result could not be read.") from exc


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
            from aicad.paths import hide_project_path

            detail = str(payload.get("error") or "The part script failed.")
            raise ScriptError(hide_project_path(detail, workspace))
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
    _hide_script_report(payload, root)
    error = payload.get("error")
    _append_log(root, code, bool(payload.get("ok")), error if isinstance(error, str) else None)
    return payload


def os_probe(workspace: str | Path, *, helper: bool = False) -> dict:
    """Ask a confined process to touch the network and a file outside the project.

    Used by the tests. It does not run assistant code.
    """
    payload = _spawn(Path(workspace), "probe", None, network=False, helper=helper)
    _drop_job(payload)
    probe = payload.get("probe")
    if not isinstance(probe, dict):
        raise ScriptError(str(payload.get("error") or "The sandbox probe did not report."))
    return probe
