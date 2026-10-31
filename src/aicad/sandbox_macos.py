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
import stat
import subprocess
import sys
import tempfile
import threading
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
    from aicad.sandbox import file_bytes

    # The same cap the script sets for itself. A failure here is not ignored:
    # an unlimited file size would let one script fill the disk.
    try:
        cap = file_bytes()
        resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
    except (OSError, ValueError) as exc:
        os.write(2, f"Could not set the file limit: {exc}\n".encode())
        os._exit(127)
    for resource_limit, value in (
        (resource.RLIMIT_DATA, memory_bytes),
        (resource.RLIMIT_CORE, 0),
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
_fdinfo_ready = False
_fdinfo_lock = threading.Lock()


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


# rusage_info_v2. Flavor 2 fills bytes that reached storage.
_RUSAGE_INFO_V2 = 2
# rusage_info_v4. Flavor 4 fills logical writes, including a file that was removed.
_RUSAGE_INFO_V4 = 4


class _RusageInfoV2(ctypes.Structure):
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
        ("ri_child_user_time", ctypes.c_uint64),
        ("ri_child_system_time", ctypes.c_uint64),
        ("ri_child_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_child_interrupt_wkups", ctypes.c_uint64),
        ("ri_child_pageins", ctypes.c_uint64),
        ("ri_child_elapsed_abstime", ctypes.c_uint64),
        ("ri_diskio_bytesread", ctypes.c_uint64),
        ("ri_diskio_byteswritten", ctypes.c_uint64),
    ]


class _RusageInfoV4(ctypes.Structure):
    """rusage_info_v4. The size has to match, or proc_pid_rusage refuses the call."""

    _fields_ = [
        *_RusageInfoV2._fields_,
        ("ri_cpu_time_qos_default", ctypes.c_uint64),
        ("ri_cpu_time_qos_maintenance", ctypes.c_uint64),
        ("ri_cpu_time_qos_background", ctypes.c_uint64),
        ("ri_cpu_time_qos_utility", ctypes.c_uint64),
        ("ri_cpu_time_qos_legacy", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_initiated", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_interactive", ctypes.c_uint64),
        ("ri_billed_system_time", ctypes.c_uint64),
        ("ri_serviced_system_time", ctypes.c_uint64),
        ("ri_logical_writes", ctypes.c_uint64),
        ("ri_lifetime_max_phys_footprint", ctypes.c_uint64),
        ("ri_instructions", ctypes.c_uint64),
        ("ri_cycles", ctypes.c_uint64),
        ("ri_billed_energy", ctypes.c_uint64),
        ("ri_serviced_energy", ctypes.c_uint64),
        ("ri_interval_max_phys_footprint", ctypes.c_uint64),
        ("ri_runnable_time", ctypes.c_uint64),
    ]


_rusage_ready = False


def bytes_written(pid: int) -> int | None:
    """Logical bytes ``pid`` has written, or None when the system will not say.

    Logical writes include a file removed before it reaches storage. When that
    counter is unavailable, bytes that reached storage are used instead.
    """
    global _rusage_ready
    lib = _proc_pidinfo()
    if lib is None:
        return None
    if not _rusage_ready:
        try:
            lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            lib.proc_pid_rusage.restype = ctypes.c_int
        except AttributeError:
            return None
        _rusage_ready = True
    logical = _RusageInfoV4()
    if lib.proc_pid_rusage(int(pid), _RUSAGE_INFO_V4, ctypes.byref(logical)) == 0:
        return int(logical.ri_logical_writes)
    info = _RusageInfoV2()
    if lib.proc_pid_rusage(int(pid), _RUSAGE_INFO_V2, ctypes.byref(info)) != 0:
        return None
    return int(info.ri_diskio_byteswritten)


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
        from aicad.sandbox import program_environment

        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
            env=program_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
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


# proc_bsdinfo. The group id is fixed at this offset in the public layout.
# A short or mismatched read is ignored; callers then use getpgid.
_PROC_PIDTBSDINFO = 3
_PROC_STATUS_ZOMBIE = 5


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def process_runtime(pid: int) -> tuple[int, bool] | None:
    """``(process group, is zombie)`` for ``pid``.

    None when the process is already gone or libproc cannot be asked. The
    group still answers after the process has left the caller's session.
    """
    lib = _proc_pidinfo()
    if lib is None or pid <= 1:
        return None
    info = _ProcBsdInfo()
    ctypes.set_errno(0)
    try:
        written = int(lib.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)))
    except (OSError, ctypes.ArgumentError):
        return None
    # pbi_pgid ends at byte 104. A pid that does not match is the wrong layout.
    if written < 104 or int(info.pbi_pid) != pid:
        return None
    return int(info.pbi_pgid), int(info.pbi_status) == _PROC_STATUS_ZOMBIE


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


# libproc flavors. The layouts are the LP64 ones from <libproc.h>.
_PROC_PIDLISTFDS = 1
_PROC_PIDFDVNODEPATHINFO = 2
_PROX_FDTYPE_VNODE = 1
_MAXPATHLEN = 1024


class _ProcFdInfo(ctypes.Structure):
    _fields_ = [
        ("proc_fd", ctypes.c_int32),
        ("proc_fdtype", ctypes.c_uint32),
    ]


class _ProcFileInfo(ctypes.Structure):
    _fields_ = [
        ("fi_openflags", ctypes.c_uint32),
        ("fi_status", ctypes.c_uint32),
        ("fi_offset", ctypes.c_int64),
        ("fi_type", ctypes.c_int32),
        ("fi_guardflags", ctypes.c_uint32),
    ]


class _VinfoStat(ctypes.Structure):
    _fields_ = [
        ("vst_dev", ctypes.c_uint32),
        ("vst_mode", ctypes.c_uint16),
        ("vst_nlink", ctypes.c_uint16),
        ("vst_ino", ctypes.c_uint64),
        ("vst_uid", ctypes.c_uint32),
        ("vst_gid", ctypes.c_uint32),
        ("vst_atime", ctypes.c_int64),
        ("vst_atimensec", ctypes.c_int64),
        ("vst_mtime", ctypes.c_int64),
        ("vst_mtimensec", ctypes.c_int64),
        ("vst_ctime", ctypes.c_int64),
        ("vst_ctimensec", ctypes.c_int64),
        ("vst_birthtime", ctypes.c_int64),
        ("vst_birthtimensec", ctypes.c_int64),
        ("vst_size", ctypes.c_int64),
        ("vst_blocks", ctypes.c_int64),
        ("vst_blksize", ctypes.c_int32),
        ("vst_flags", ctypes.c_uint32),
        ("vst_gen", ctypes.c_uint32),
        ("vst_rdev", ctypes.c_int32),
        ("vst_qspare", ctypes.c_int64 * 2),
    ]


class _VnodeInfo(ctypes.Structure):
    _fields_ = [
        ("vi_stat", _VinfoStat),
        ("vi_type", ctypes.c_int),
        ("vi_pad", ctypes.c_int),
        ("vi_fsid", ctypes.c_int32 * 2),
    ]


class _VnodeInfoPath(ctypes.Structure):
    _fields_ = [
        ("vip_vi", _VnodeInfo),
        ("vip_path", ctypes.c_char * _MAXPATHLEN),
    ]


class _VnodeFdInfoWithPath(ctypes.Structure):
    _fields_ = [
        ("pfi", _ProcFileInfo),
        ("pvip", _VnodeInfoPath),
    ]


def _bind_fdinfo(lib) -> bool:
    """Bind ``proc_pidfdinfo`` once. Two threads must not set its signature together."""
    global _fdinfo_ready
    with _fdinfo_lock:
        if _fdinfo_ready:
            return True
        if not hasattr(lib, "proc_pidfdinfo"):
            return False
        lib.proc_pidfdinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        lib.proc_pidfdinfo.restype = ctypes.c_int
        _fdinfo_ready = True
        return True


def open_file_sizes(pid: int, root: Path) -> dict[str, int]:
    """Sizes of regular files ``pid`` has open under ``root``.

    An unlinked file stays visible until its descriptor is closed. The path
    is the one the process opened. A file outside ``root`` is not counted.
    """
    if pid <= 0 or sys.platform != "darwin" or pid != os.getpid():
        # Another process's descriptor list has stopped the parent.
        return {}
    lib = _proc_pidinfo()
    if lib is None or not _bind_fdinfo(lib):
        return {}
    count = 64
    used = 0
    infos = None
    while count <= 8192:
        infos = (_ProcFdInfo * count)()
        try:
            used = int(lib.proc_pidinfo(pid, _PROC_PIDLISTFDS, 0, ctypes.byref(infos), ctypes.sizeof(infos)))
        except (OSError, ctypes.ArgumentError):
            return {}
        if used <= 0:
            return {}
        if used < ctypes.sizeof(infos):
            break
        count *= 2
    if infos is None or used <= 0:
        return {}
    root_resolved = root.resolve()
    sizes: dict[str, int] = {}
    slots = min(len(infos), used // ctypes.sizeof(_ProcFdInfo))
    for index in range(slots):
        item = infos[index]
        if int(item.proc_fdtype) != _PROX_FDTYPE_VNODE or int(item.proc_fd) < 0:
            continue
        # The kernel writes at most this many bytes. A short struct must not be the target.
        buf = ctypes.create_string_buffer(4096)
        try:
            written = int(
                lib.proc_pidfdinfo(
                    pid,
                    int(item.proc_fd),
                    _PROC_PIDFDVNODEPATHINFO,
                    buf,
                    len(buf),
                )
            )
        except (OSError, ctypes.ArgumentError):
            continue
        need = ctypes.sizeof(_VnodeFdInfoWithPath)
        if written < need or written > len(buf):
            continue
        info = _VnodeFdInfoWithPath.from_buffer_copy(buf.raw[:need])
        mode = int(info.pvip.vip_vi.vi_stat.vst_mode)
        if not stat.S_ISREG(mode):
            continue
        raw = bytes(info.pvip.vip_path).split(b"\x00", 1)[0]
        text = os.fsdecode(raw)
        if text.endswith(" (deleted)"):
            text = text[: -len(" (deleted)")]
        if not text.startswith("/"):
            continue
        path = Path(text)
        try:
            path.resolve().relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        key = os.path.normcase(os.path.abspath(text))
        size = int(info.pvip.vip_vi.vi_stat.vst_size)
        if size > sizes.get(key, 0):
            sizes[key] = size
    return sizes


def run_directory_watch(root, retain, release, reread, scan, stop, mark) -> None:
    """Watch ``root`` until ``stop`` is set.

    A new file is opened while its name is still there, so a name that is
    gone before the next timer is still measured. This does not use kqueue:
    a kqueue wait in this process while a script process is created stops
    the parent. This thread does not ask libproc; the other sampler does.
    """
    if sys.platform != "darwin":
        mark(False)
        return
    known: dict[str, set[str]] = {}

    def rescan() -> None:
        pending = [str(root)]
        visited: set[str] = set()
        while pending:
            if stop.is_set():
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
            for entry in entries:
                if entry.is_symlink():
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    pending.append(entry.path)
                elif is_file:
                    live.add(entry.path)
                    if previous is not None and entry.path not in previous:
                        retain(Path(entry.path))
            if previous is not None:
                for gone in previous - live:
                    release(Path(gone))
            known[directory] = live

    rescan()
    mark(True)
    while not stop.is_set():
        if stop.wait(0.005):
            break
        try:
            rescan()
            reread()
        except Exception:
            continue
