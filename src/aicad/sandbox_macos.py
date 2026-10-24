"""macOS confinement for an untrusted design script.

``sandbox-exec`` applies a Seatbelt profile: the script may read the system
locations Python needs, and it may read and write only the project folder.
The network is denied. CPU time comes from ``setrlimit``. The address-space
cap is not applied: macOS maps a system library cache larger than that cap
into every process, and setting the cap stops the script before it runs.
The parent watches the script's resident memory instead and stops it above
the same 4 GiB limit.
Apple has deprecated ``sandbox-exec``. It is still the practical per-process
profile that does not need a container or an extra install. If it is missing,
or the limits cannot be set, the script is not run.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

_SYSTEM_ROOTS = (
    "/usr",
    "/bin",
    "/sbin",
    "/System",
    "/Library",
    "/opt",
    "/opt/homebrew",
    "/usr/local",
    "/dev",
)


def availability_error(project: str | Path | None = None) -> str | None:
    from aicad.prereqs import find_program

    root = Path(project) if project is not None else None
    if find_program("sandbox-exec", project=root) is None:
        return (
            "This Mac does not have sandbox-exec, so aicad cannot limit the script "
            "to the project folder. The script was not run."
        )
    return None


def _escape(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_profile(read_paths: Sequence[str], write_paths: Sequence[str], *, network: bool) -> str:
    """Return a Seatbelt profile for one run."""

    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        # Python and the dynamic linker look up a long list of system services.
        # The profile still denies the network below. Mach lookup is a gap: a
        # system service could do work the script is not allowed to do itself.
        "(allow mach-lookup)",
        "(allow mach-host*)",
        "(allow ipc-posix-shm*)",
        # Python and the geometry libraries use semaphores. Denying them aborts
        # the process instead of returning an error.
        "(allow ipc-posix-sem)",
        "(allow system-socket)",
        "(allow mach-register)",
        "(allow dynamic-code-generation)",
        # A pipe is a FIFO. The name PIPE is not a Seatbelt type, and naming it
        # makes sandbox-exec refuse the profile.
        "(allow file-read-data file-write-data file-ioctl (vnode-type FIFO))",
        "(allow file-read-data file-write-data file-ioctl (vnode-type CHARACTER-DEVICE))",
        # Reading the root directory itself is required. Without it the process
        # aborts (signal 6) instead of starting. This does not grant the files
        # inside other directories.
        "(allow file-read* (literal \"/\"))",
        "(allow file-read-metadata (subpath \"/\"))",
        "(allow file-ioctl (literal \"/dev/null\") (literal \"/dev/zero\") "
        "(literal \"/dev/random\") (literal \"/dev/urandom\") (literal \"/dev/dtracehelper\"))",
    ]
    roots = [path for path in _SYSTEM_ROOTS if os.path.isdir(path)]
    if roots:
        joined = " ".join(f"(subpath {_escape(path)})" for path in roots)
        lines.append(f"(allow file-read* file-map-executable file-ioctl {joined})")
    for path in read_paths:
        lines.append(
            "(allow file-read* file-map-executable file-ioctl "
            f"(subpath {_escape(path)}))"
        )
    for path in write_paths:
        lines.append(
            "(allow file-read* file-write* file-map-executable file-ioctl "
            f"(subpath {_escape(path)}))"
        )
    if network:
        lines.append("(allow network*)")
    else:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def _resource():
    """The Unix ``resource`` module. Windows does not provide it.

    The profile text can be built on any system. The limits are applied only
    on macOS, where this module exists.
    """
    import resource

    return resource


def apply_limits(*, cpu_seconds: int, memory_bytes: int) -> None:
    """Set CPU and memory limits. On failure the process exits before the script."""

    resource = _resource()
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    except (OSError, ValueError) as exc:
        os.write(2, f"Could not set CPU or memory limits: {exc}\n".encode())
        os._exit(127)
    file_cap = 8 * 1024 * 1024 * 1024
    for resource_limit, value in (
        (resource.RLIMIT_DATA, memory_bytes),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_FSIZE, file_cap),
    ):
        try:
            resource.setrlimit(resource_limit, (value, value))
        except (OSError, ValueError):
            pass


def make_preexec(*, cpu_seconds: int, memory_bytes: int) -> Callable[[], None]:
    def preexec() -> None:
        apply_limits(cpu_seconds=cpu_seconds, memory_bytes=memory_bytes)

    return preexec


def command_prefix(profile_path: str, project: str | Path | None = None) -> list[str]:
    from aicad.prereqs import find_program

    root = Path(project) if project is not None else None
    sandbox_exec = find_program("sandbox-exec", project=root)
    if sandbox_exec is None:
        sandbox_exec = "/usr/bin/sandbox-exec"
    return [sandbox_exec, "-f", profile_path]


def identity_failures(cpu_seconds: int, memory_bytes: int) -> list[str]:
    """CPU limit only. ``memory_bytes`` is the cap other systems apply; macOS does not set it."""
    _ = memory_bytes
    failures = []
    resource = _resource()
    _cpu_soft, cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)
    if cpu_hard == resource.RLIM_INFINITY or cpu_hard > cpu_seconds:
        failures.append("the CPU time limit is not in effect")
    return failures


def network_error_is_confined(error: OSError) -> bool:
    return error.errno in {getattr(os, "EPERM", 1), getattr(os, "EACCES", 13)}


# proc_taskinfo from <libproc.h>. Resident size is the second 8-byte field.
_PROC_PIDTASKINFO = 4


class _ProcTaskInfo(ctypes.Structure):
    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
        ("pti_threads_user", ctypes.c_uint64),
        ("pti_threads_system", ctypes.c_uint64),
        ("pti_policy", ctypes.c_int32),
        ("pti_faults", ctypes.c_int32),
        ("pti_pageins", ctypes.c_int32),
        ("pti_cow_faults", ctypes.c_int32),
        ("pti_messages_sent", ctypes.c_int32),
        ("pti_messages_received", ctypes.c_int32),
        ("pti_syscalls_mach", ctypes.c_int32),
        ("pti_syscalls_unix", ctypes.c_int32),
        ("pti_csw", ctypes.c_int32),
        ("pti_threadnum", ctypes.c_int32),
        ("pti_numrunning", ctypes.c_int32),
        ("pti_priority", ctypes.c_int32),
    ]


_libproc = None
_libproc_failed = False


def _proc_pidinfo():
    """The libproc handle, or None when this Mac will not load it."""
    global _libproc, _libproc_failed
    if _libproc_failed:
        return None
    if _libproc is not None:
        return _libproc
    try:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        lib.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        lib.proc_pidinfo.restype = ctypes.c_int
    except OSError:
        _libproc_failed = True
        return None
    _libproc = lib
    return lib


def _resident_via_proc_pidinfo(pid: int) -> int | None:
    lib = _proc_pidinfo()
    if lib is None:
        return None
    info = _ProcTaskInfo()
    written = lib.proc_pidinfo(pid, _PROC_PIDTASKINFO, 0, ctypes.byref(info), ctypes.sizeof(info))
    if written < 16:
        return None
    return int(info.pti_resident_size)


def _resident_via_ps(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    text = completed.stdout.strip().split()
    if not text:
        return None
    try:
        # ps reports resident memory in kilobytes.
        return int(text[0]) * 1024
    except ValueError:
        return None


def resident_bytes(pid: int) -> int | None:
    """Resident memory of one process, in bytes. None if it is already gone."""
    if pid <= 0:
        return None
    size = _resident_via_proc_pidinfo(pid)
    if size is not None:
        return size
    return _resident_via_ps(pid)


# proc_uniqidentifierinfo. The two ids sit at the same offset in the public layouts.
_PROC_PIDUNIQIDENTIFIERINFO = 17


class _ProcUniqueId(ctypes.Structure):
    _fields_ = [
        ("p_uuid", ctypes.c_uint8 * 16),
        ("p_uniqueid", ctypes.c_uint64),
        ("p_puniqueid", ctypes.c_uint64),
        ("p_tail", ctypes.c_uint8 * 24),
    ]


def _bind(lib, name: str, argtypes: list) -> Callable | None:
    try:
        fn = getattr(lib, name)
    except AttributeError:
        return None
    fn.argtypes = argtypes
    fn.restype = ctypes.c_int
    return fn


def _read_pids(call: Callable[[ctypes.Array, int], int], *, capacity: int = 256) -> list[int] | None:
    """Read a pid list whose return value may be a count or a byte count.

    None means the listing itself failed. An empty list means there were no pids.
    """
    for _attempt in range(5):
        buf = (ctypes.c_int * capacity)()
        ctypes.set_errno(0)
        try:
            written = int(call(buf, ctypes.sizeof(buf)))
        except (OSError, ctypes.ArgumentError):
            return None
        if written < 0 or (written == 0 and ctypes.get_errno()):
            return None
        if written == 0:
            return []
        if written > capacity:
            capacity = min(max(written, written // 4) + 32, 100_000)
            continue
        pids = [int(buf[index]) for index in range(written) if int(buf[index]) > 1]
        if written < capacity:
            return pids
        capacity = min(capacity * 4, 100_000)
    return None


def child_pids(ppid: int) -> list[int] | None:
    """Direct children of ``ppid``.

    None means libproc could not be asked. An empty list means there are no children.
    """
    lib = _proc_pidinfo()
    if lib is None or ppid <= 1:
        return None
    fn = _bind(lib, "proc_listchildpids", [ctypes.c_int, ctypes.c_void_p, ctypes.c_int])
    if fn is None:
        return None

    def call(buf: ctypes.Array, size: int) -> int:
        return fn(ppid, ctypes.byref(buf), size)

    return _read_pids(call)


def all_pids() -> list[int] | None:
    """Every pid libproc can see. None when that listing fails."""
    lib = _proc_pidinfo()
    if lib is None:
        return None
    fn = _bind(lib, "proc_listpids", [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int])
    if fn is None:
        return None

    def call(buf: ctypes.Array, size: int) -> int:
        return fn(1, 0, ctypes.byref(buf), size)

    return _read_pids(call, capacity=1024)


def pids_holding_path(path: str) -> list[int] | None:
    """Pids that have ``path`` open. None when libproc cannot answer."""
    lib = _proc_pidinfo()
    if lib is None or not path:
        return None
    fn = _bind(
        lib,
        "proc_listpidspath",
        [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int],
    )
    if fn is None:
        return None
    encoded = os.fsencode(path)

    def call(buf: ctypes.Array, size: int) -> int:
        return fn(1, 0, encoded, 0, ctypes.byref(buf), size)

    return _read_pids(call)


def process_identity(pid: int) -> tuple[int, int] | None:
    """``(unique_id, parent_unique_id)`` for ``pid``.

    The parent id is the process that created it. It does not change when the
    process leaves its group or is reparented. None if the process is already
    gone or libproc cannot be asked.
    """
    lib = _proc_pidinfo()
    if lib is None or pid <= 1:
        return None
    info = _ProcUniqueId()
    ctypes.set_errno(0)
    try:
        written = int(lib.proc_pidinfo(pid, _PROC_PIDUNIQIDENTIFIERINFO, 0, ctypes.byref(info), ctypes.sizeof(info)))
    except (OSError, ctypes.ArgumentError):
        return None
    if written < 32 or info.p_uniqueid == 0:
        return None
    return int(info.p_uniqueid), int(info.p_puniqueid)


def open_sentinel() -> tuple[int, str] | None:
    """An inheritable file descendants keep open, or None if it cannot be created.

    Fork keeps this descriptor open, including after the child leaves the
    process group. The caller lists holders with libproc. A close-guard is not
    used: that guard also forces the descriptor closed in a forked child.
    """
    try:
        fd, name = tempfile.mkstemp(prefix="aicad-reap-", suffix=".hold")
    except OSError:
        return None
    try:
        os.write(fd, b"\n")
        os.set_inheritable(fd, True)
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(name)
        except OSError:
            pass
        return None
    return fd, name


def close_sentinel(fd: int, path: str) -> None:
    """Drop the parent's copy of the sentinel. Ignore a descriptor that is already closed."""
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass
