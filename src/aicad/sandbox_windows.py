"""Windows confinement for an untrusted design script.

A Job Object caps memory, CPU time, and how many processes the script can
start. An AppContainer blocks network access and blocks reading or writing the
user profile outside the folders we grant. We use the stricter
less-privileged AppContainer when Python can still start that way. A normal
AppContainer can still read files Windows has already opened to every
AppContainer, mostly under ``C:\\Windows``. If neither container can be
created, or the job cannot be applied, the script is not run.

There is no in-process fallback. A job cannot block OpenProcess on a
process outside the job. The container can open a process only when that
process grants the container, and this aicad process does not. aicad does
not add its own deny entry: that change stopped later scripts from finishing.

The script is the base interpreter. The virtual-environment redirector
starts a second process in a job of its own, so the script would not stay
in the sandbox job. The new process stays suspended until that job holds
it and its token is an AppContainer.

When the script was not given the network, Windows Firewall and a filter
block that container's inbound and outbound traffic for every protocol
before the process runs. The block is not limited to TCP or to a port.
The script still checks the token. It runs only when a TCP connection and
both a connected and an unconnected UDP send are refused. A successful
send is not a refusal. A timeout is not a refusal.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import re
import subprocess
import sys
import threading
import uuid
from collections.abc import Sequence
from ctypes import wintypes
from pathlib import Path

_PROFILE_NAME = "aicadscript"
_PROFILE_DISPLAY = "aicad design script"
_PROFILE_DESCRIPTION = "Limits an aicad design script to one project folder."

_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_NO_WINDOW = 0x08000000
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
# Leave the runner's job when it allows it, then enter only the sandbox job.
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_ERROR_ACCESS_DENIED = 5
_STARTF_USESTDHANDLES = 0x00000100
_PROBE_CPU_SECONDS = 30
_PROBE_MEMORY_BYTES = 2 * 1024 * 1024 * 1024

_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY = 0x0002000F
_PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT = 1

_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JobObjectBasicAccountingInformation = 1
_JobObjectExtendedLimitInformation = 9
# 100 ms, in the job's 100-nanosecond ticks. A CPU kill can land a little
# under the cap. A short crash stays well under it.
_CPU_LIMIT_SLACK_TICKS = 1_000_000

_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x00000102
_TOKEN_QUERY = 0x0008
_TokenIsAppContainer = 29
_TokenCapabilities = 30
_TokenAppContainerSid = 31
_ERROR_ALREADY_EXISTS = 0x800700B7
_INVALID_HANDLE = ctypes.c_void_p(-1).value

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_GENERIC_ALL = 0x10000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_CREATE_ALWAYS = 2
_FILE_ATTRIBUTE_NORMAL = 0x80

_mode: str | None = None
_mode_error: str | None = None
_sid_string: str | None = None
# One loaded library per process. A new WinDLL() drops the prototypes set below,
# and a handle returned as a C int is not a pointer-sized HANDLE.
_kernel32_lib: ctypes.WinDLL | None = None
_userenv_lib: ctypes.WinDLL | None = None
_advapi32_lib: ctypes.WinDLL | None = None
_bound = False
# The availability probe starts a container. Later tests in this process reuse it.
_probe_done = False
_probe_result: str | None = None
# Parents already checked for this container, so a later script does not open them again.
_granted_traverse: set[tuple[str, str, str]] = set()
# One firewall block for this container, shared by overlapping network-off children.
_network_off_lock = threading.Lock()
_network_off_holders = 0
_network_off_sid: str | None = None
# Rules left in place after the last network-off child. The next child with
# the same package reuses them. A network-on script removes them first.
_network_off_rules_sid: str | None = None
# The next process has no memory of that. The record is only the package id.
# It is not proof the rules still exist. It lives in the real local profile,
# which is not the project and is not a folder the script is allowed to write.
_network_off_marker_path: Path | None = None
# Held from profile creation until the confined process has exited, so another
# start does not delete the profile that process is using.
_profile_lock_handle = None
_profile_local_app_data_known = False
_profile_local_app_data_path: Path | None = None
_FOLDERID_LOCAL_APPDATA = "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"
_PACKAGE_SID_TEXT = re.compile(r"^S-1-15-2(?:-\d+)+$", re.IGNORECASE)
# Inbound and outbound, every protocol. The old UDP-only name is removed
# when present and is not installed again.
_NETWORK_OFF_RULES = (
    "aicad-script-network-off-out",
    "aicad-script-network-off-in",
)
_NETWORK_OFF_STALE_RULES = ("aicad-script-network-off-udp",)

# One directory finishes immediately. A tree walk of the Python install can
# take longer. Either one that does not finish is waiting, not working.
_ICACLS_TIMEOUT_SECONDS = 15
_ICACLS_TREE_TIMEOUT_SECONDS = 60
_GRANT_TIMEOUT_SECONDS = 15

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_FILE_SHARE_DELETE = 0x00000004
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_ACCESS_ALLOWED_ACE_TYPE = 0
_OBJECT_INHERIT_ACE = 0x1
_CONTAINER_INHERIT_ACE = 0x2
_INHERIT_ONLY_ACE = 0x8
# Modify, the rights icacls calls M: read, write, execute, and delete.
_MODIFY_MASK = 0x1301BF
_FILE_LIST_DIRECTORY = 0x00000001
_FILE_TRAVERSE = 0x00000020
_FILE_READ_ATTRIBUTES = 0x00000080
_SYNCHRONIZE = 0x00100000
_GENERIC_ALL = 0x10000000
_GENERIC_EXECUTE = 0x20000000
# Walk this directory and read its attributes. Listing its children is not included.
_TRAVERSE_MASK = _FILE_TRAVERSE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
_ALL_APPLICATION_PACKAGES = "S-1-15-2-1"
_ALL_RESTRICTED_APPLICATION_PACKAGES = "S-1-15-2-2"
_ACL_REVISION = 2
_ACL_REVISION_DS = 4
_SECURITY_DESCRIPTOR_REVISION = 1
# AclSizeInformation. A plain integer: the structure class must not be passed here.
_ACL_SIZE_INFORMATION = 2
# Opening these is not a failure of the grant. Windows already allows the
# container through, or the folder is not one this process can edit.
_UNCHANGEABLE_ERRORS = {2, 3, 5, 32}


class IcaclsTimeout(OSError):
    """icacls did not finish, so the folder was not granted."""


class GrantTimeout(OSError):
    """A folder grant did not finish, so that folder was not changed."""


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobBasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("_pad1", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("_pad2", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobBasicAccounting(ctypes.Structure):
    """JOBOBJECT_BASIC_ACCOUNTING_INFORMATION. 48 bytes, including on 64-bit."""

    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


class _JobExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.c_void_p),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfo),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
        ("_pad", wintypes.DWORD),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


def _kernel32() -> ctypes.WinDLL:
    global _kernel32_lib
    if _kernel32_lib is None:
        _kernel32_lib = ctypes.WinDLL("kernel32", use_last_error=True)
    return _kernel32_lib


def _userenv() -> ctypes.WinDLL:
    global _userenv_lib
    if _userenv_lib is None:
        _userenv_lib = ctypes.WinDLL("userenv", use_last_error=True)
    return _userenv_lib


def _advapi32() -> ctypes.WinDLL:
    global _advapi32_lib
    if _advapi32_lib is None:
        _advapi32_lib = ctypes.WinDLL("advapi32", use_last_error=True)
    return _advapi32_lib


def _win_error(prefix: str) -> str:
    return f"{prefix} (Windows error {_last_error()})."


def _last_error(kernel32=None) -> int:
    if kernel32 is not None:
        recorded = getattr(kernel32, "last_error", None)
        if recorded is not None:
            return int(recorded) & 0xFFFFFFFF
    getter = getattr(ctypes, "get_last_error", None)
    if getter is None:
        return 0
    return int(getter()) & 0xFFFFFFFF


def _update_attribute_signature() -> tuple[object, list[object]]:
    """Argument types for UpdateProcThreadAttribute. Tests call this prototype."""

    return (
        wintypes.BOOL,
        [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
        ],
    )


def _bind() -> None:
    global _bound
    if _bound:
        return
    kernel32 = _kernel32()
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    update_restype, update_argtypes = _update_attribute_signature()
    kernel32.UpdateProcThreadAttribute.argtypes = update_argtypes
    kernel32.UpdateProcThreadAttribute.restype = update_restype
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_wchar),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfoEx),
        ctypes.POINTER(_ProcessInformation),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL

    userenv = _userenv()
    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long

    advapi32 = _advapi32()
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    advapi32.FreeSid.restype = ctypes.c_void_p
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    _bound = True


def _sid_to_string(sid: ctypes.c_void_p) -> str:
    out = ctypes.c_void_p()
    if not _advapi32().ConvertSidToStringSidW(sid, ctypes.byref(out)):
        raise OSError(_win_error("Could not read the AppContainer id"))
    try:
        return ctypes.cast(out, wintypes.LPWSTR).value or ""
    finally:
        _kernel32().LocalFree(out)


def _create_appcontainer_profile() -> tuple[int, ctypes.c_void_p]:
    """Create the script profile with no stored capabilities."""

    userenv = _userenv()
    sid = ctypes.c_void_p()
    result = int(
        userenv.CreateAppContainerProfile(
            _PROFILE_NAME,
            _PROFILE_DISPLAY,
            _PROFILE_DESCRIPTION,
            None,
            0,
            ctypes.byref(sid),
        )
    )
    return result & 0xFFFFFFFF, sid


def _delete_appcontainer_profile() -> int:
    """Delete the named script profile. A missing profile is not an error."""

    return int(_userenv().DeleteAppContainerProfile(_PROFILE_NAME)) & 0xFFFFFFFF


def _free_partial_sid(sid: ctypes.c_void_p | None) -> None:
    if sid is None or not getattr(sid, "value", None):
        return
    _advapi32().FreeSid(sid)


def _profile_lock_path() -> Path:
    """A lock outside every project. The script cannot unlink it."""

    base = _profile_local_app_data()
    if base is None:
        import tempfile

        base = Path(tempfile.gettempdir())
    return base / "aicad" / "script-profile.lock"


def _acquire_profile_lock() -> None:
    """Wait until no confined script is using the shared profile name."""

    global _profile_lock_handle
    if _profile_lock_handle is not None:
        raise OSError("The script profile is already in use, so the script was not started.")
    path = _profile_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if handle.seek(0, os.SEEK_END) < 1:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            import time

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    # 13 and 36 are the Windows errors for a region another process holds.
                    if exc.errno not in (13, 36):
                        raise
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except Exception:
        handle.close()
        raise
    _profile_lock_handle = handle


def _release_profile_lock() -> None:
    global _profile_lock_handle
    handle = _profile_lock_handle
    _profile_lock_handle = None
    if handle is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _profile_use_path() -> Path:
    return _profile_lock_path().with_name("script-profile-use")


class _PROCESSENTRY32W(ctypes.Structure):
    """PROCESSENTRY32W. The heap id is pointer-sized so the parent id stays aligned."""

    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def process_parents() -> dict[int, int]:
    """Pid to parent pid from a process snapshot. Empty when the snapshot fails."""

    if sys.platform != "win32":
        return {}
    _bind()
    kernel32 = _kernel32()
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    # TH32CS_SNAPPROCESS
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot is None or ctypes.cast(snapshot, ctypes.c_void_p).value in {None, _INVALID_HANDLE}:
        return {}
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    parents: dict[int, int] = {}
    try:
        ok = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    return parents


def terminate_pid(pid: int) -> None:
    """End ``pid`` and wait until it has left. A missing process is already gone.

    The open asks only for terminate and wait. Asking for every process right
    fails for a part script in an AppContainer, and that script would keep
    the design-check file open.
    """

    if sys.platform != "win32" or pid <= 1 or pid == os.getpid():
        return
    _bind()
    kernel32 = _kernel32()
    # PROCESS_TERMINATE | SYNCHRONIZE
    handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, int(pid))
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
        kernel32.WaitForSingleObject(handle, 5000)
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    """True when that process is still running.

    On Windows, ``os.kill(pid, 0)`` is not an existence check. It raises
    ``SystemError`` and can terminate the process. Open the process instead.
    """

    if pid <= 0:
        return False
    if sys.platform == "win32":
        _bind()
        kernel32 = _kernel32()
        # PROCESS_QUERY_LIMITED_INFORMATION
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return int(code.value) == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _load_profile_use() -> dict[int, int]:
    """Live processes that are using the script profile. A dead pid is dropped."""

    found: dict[int, int] = {}
    try:
        text = _profile_use_path().read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
            count = int(parts[1])
        except ValueError:
            continue
        if count > 0 and _pid_alive(pid):
            found[pid] = count
    return found


def _store_profile_use(found: dict[int, int]) -> None:
    path = _profile_use_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(f"{pid} {count}\n" for pid, count in sorted(found.items()))
    path.write_text(data, encoding="utf-8")


def _profile_is_in_use() -> bool:
    """True when a live process is already running in the script profile."""

    found = _load_profile_use()
    try:
        _store_profile_use(found)
    except OSError:
        pass
    return bool(found)


def _change_profile_use(delta: int) -> None:
    found = _load_profile_use()
    pid = os.getpid()
    count = found.get(pid, 0) + delta
    if count > 0:
        found[pid] = count
    else:
        found.pop(pid, None)
    _store_profile_use(found)


def _release_profile_use() -> None:
    """Drop this process's use of the profile. The caller does not hold the lock."""

    _acquire_profile_lock()
    try:
        _change_profile_use(-1)
    finally:
        _release_profile_lock()


def _derive_profile_sid() -> tuple[int, ctypes.c_void_p]:
    sid = ctypes.c_void_p()
    result = int(
        _userenv().DeriveAppContainerSidFromAppContainerName(
            _PROFILE_NAME,
            ctypes.byref(sid),
        )
    )
    return result & 0xFFFFFFFF, sid


def _process_capability_sids(process: int) -> list[str]:
    """Capability SIDs on a suspended process token."""

    kernel32 = _kernel32()
    advapi32 = _advapi32()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
        raise OSError(_win_error("Could not read the script profile token"))
    try:
        returned = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, _TokenCapabilities, None, 0, ctypes.byref(returned))
        size = int(returned.value)
        if size <= 0 or size > 1024 * 1024:
            raise OSError("Could not read the script profile capabilities.")
        buffer = ctypes.create_string_buffer(size)
        if not advapi32.GetTokenInformation(
            token,
            _TokenCapabilities,
            buffer,
            size,
            ctypes.byref(returned),
        ):
            raise OSError("Could not read the script profile capabilities.")
        return _token_group_sids(
            buffer,
            lambda pointer: _sid_to_string(ctypes.c_void_p(pointer)),
        )
    finally:
        kernel32.CloseHandle(token)


def _stored_capabilities_are_empty(sid: ctypes.c_void_p) -> bool:
    """True when this profile adds no capabilities.

    A suspended process is created with no capabilities and terminated
    before it runs. Any capability on its token was stored in the profile.
    """

    kernel32 = _kernel32()
    security = _SecurityCapabilities()
    security.AppContainerSid = sid
    security.Capabilities = None
    security.CapabilityCount = 0
    size = ctypes.c_size_t(0)
    kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    if not kernel32.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
        raise OSError(_win_error("Could not prepare the script profile check"))
    try:
        if not kernel32.UpdateProcThreadAttribute(
            attributes,
            0,
            _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
            ctypes.c_void_p(ctypes.addressof(security)),
            ctypes.sizeof(security),
            None,
            None,
        ):
            raise OSError(_win_error("Could not attach the script profile"))
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = ctypes.c_void_p(ctypes.addressof(attributes))
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline([sys.executable, "-c", ""]))
        process = _ProcessInformation()
        created = False
        for breakaway in (True, False):
            created = bool(
                kernel32.CreateProcessW(
                    sys.executable,
                    command_line,
                    None,
                    None,
                    False,
                    _creation_flags(breakaway=breakaway),
                    None,
                    None,
                    ctypes.byref(startup),
                    ctypes.byref(process),
                )
            )
            if created:
                break
            code = _last_error(kernel32)
            if breakaway and code == _ERROR_ACCESS_DENIED:
                continue
            raise OSError(f"Could not check the script profile (Windows error {code}).")
        if not created:
            raise OSError("Could not check the script profile.")
        try:
            return _process_capability_sids(int(process.hProcess)) == []
        finally:
            kernel32.TerminateProcess(process.hProcess, 1)
            kernel32.CloseHandle(process.hThread)
            kernel32.CloseHandle(process.hProcess)
    finally:
        kernel32.DeleteProcThreadAttributeList(attributes)


def _open_proven_profile() -> tuple[ctypes.c_void_p, str]:
    """Open the profile a live script is using, when it has no capabilities.

    The profile is not deleted. A profile with stored capabilities is not used.
    """

    result, sid = _derive_profile_sid()
    if result != 0:
        _free_partial_sid(sid)
        raise OSError(
            f"Windows could not read the AppContainer profile (code 0x{result & 0xFFFFFFFF:08X})."
        )
    try:
        if not _stored_capabilities_are_empty(sid):
            raise OSError(
                "The Windows script profile has capabilities, so the script was not started."
            )
        text = _sid_to_string(sid)
    except Exception:
        _free_partial_sid(sid)
        raise
    return sid, text


def _appcontainer_sid() -> tuple[ctypes.c_void_p, str]:
    """A script profile with no stored capabilities.

    When no script is using the name, an existing profile is deleted and
    created again. A profile a live script is using is opened only after its
    token has no capabilities. The lock covers that decision, not the script.
    """

    _acquire_profile_lock()
    try:
        result, sid = _create_appcontainer_profile()
        if result != 0:
            _free_partial_sid(sid)
            if _profile_is_in_use():
                sid, text = _open_proven_profile()
            else:
                _delete_appcontainer_profile()
                result, sid = _create_appcontainer_profile()
                if result != 0:
                    _free_partial_sid(sid)
                    raise OSError(
                        "Windows could not create an AppContainer profile "
                        f"(code 0x{result & 0xFFFFFFFF:08X})."
                    )
                text = _sid_to_string(sid)
        else:
            text = _sid_to_string(sid)
        _change_profile_use(1)
        return sid, text
    finally:
        _release_profile_lock()


def _is_volume_root_text(path: str) -> bool:
    """True for ``C:\\`` and ``\\\\server\\share``.

    icacls asks "Are you sure?" on a volume root and then waits for a person.
    Saying yes would add an access entry on the whole drive. The container
    does not get that entry.
    """

    drive, tail = ntpath.splitdrive(path)
    if not drive:
        return False
    return tail in ("", "\\", "/")


def _windows_traverse_parents(path: str) -> list[str]:
    """Parents that may receive traverse, nearest first, never the volume root."""

    current = ntpath.normpath(path)
    parents: list[str] = []
    while True:
        parent = ntpath.dirname(current)
        if parent == current:
            break
        if not _is_volume_root_text(parent):
            parents.append(parent)
        current = parent
    return parents


def _run_icacls(command: list[str], path: Path, *, recurse: bool) -> subprocess.CompletedProcess[str]:
    timeout = _ICACLS_TREE_TIMEOUT_SECONDS if recurse else _ICACLS_TIMEOUT_SECONDS
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise IcaclsTimeout(
            f"icacls did not finish within {timeout} seconds for {path}. "
            "It may be waiting for confirmation, so the sandbox did not change that folder."
        ) from exc


def _icacls(path: Path, sid: str, rights: str, *, recurse: bool) -> None:
    command = ["icacls", str(path), "/grant", f"*{sid}:{rights}", "/C", "/Q"]
    if recurse:
        command.append("/T")
    completed = _run_icacls(command, path, recurse=recurse)
    if completed.returncode != 0:
        detail = (completed.stdout or "") + (completed.stderr or "")
        raise OSError(f"Could not grant the sandbox access to {path}. {detail.strip()}")


def _grant_tree(path: Path, sid: str, rights: str) -> None:
    if path.is_dir():
        _icacls(path, sid, rights, recurse=True)


def _package_sid(lpac: bool) -> str:
    """The package id this container already carries, besides its own id."""

    if lpac:
        return _ALL_RESTRICTED_APPLICATION_PACKAGES
    return _ALL_APPLICATION_PACKAGES


def _ace_allows_traverse(ace_type: int, ace_flags: int, mask: int) -> bool:
    """True when this entry lets its trustee walk the directory itself."""

    if ace_type != _ACCESS_ALLOWED_ACE_TYPE:
        return False
    if ace_flags & _INHERIT_ONLY_ACE:
        return False
    if mask & (_GENERIC_ALL | _GENERIC_EXECUTE):
        return True
    return bool(mask & _FILE_TRAVERSE)


def _dacl_needs_grant(
    aces: Sequence[tuple[int, int, int, str]],
    sid: str,
    package_sid: str,
) -> bool:
    """False when the directory already lets this container walk through it."""

    wanted = {sid, package_sid}
    for ace_type, ace_flags, mask, ace_sid in aces:
        if ace_sid in wanted and _ace_allows_traverse(ace_type, ace_flags, mask):
            return False
    return True


class _GrantStopped(BaseException):
    """The grant wait ended. The folder must be left unchanged."""


_grant_stop = threading.local()


def _grant_was_stopped() -> bool:
    """True after the grant wait has given up. The writer has to stop."""

    event = getattr(_grant_stop, "event", None)
    return event is not None and event.is_set()


def _stop_grant_thread(thread: threading.Thread) -> None:
    """Ask the grant thread to raise at its next Python instruction."""

    ident = thread.ident
    if not ident:
        return
    api = ctypes.pythonapi.PyThreadState_SetAsyncExc
    api.argtypes = [ctypes.c_ulong, ctypes.py_object]
    api.restype = ctypes.c_int
    count = api(ctypes.c_ulong(ident), ctypes.py_object(_GrantStopped))
    if count > 1:
        api(ctypes.c_ulong(ident), None)


def _finish_within(seconds: float, path: Path, work) -> None:
    """Run ``work`` and give up if it does not return.

    A grant that walks every child of a tool-cache directory does not return
    before the test is killed. Stopping here leaves that directory unchanged.
    The thread is told to stop, and it does not write the access entry after that.
    """

    box: dict[str, Exception] = {}
    stop = threading.Event()

    def run() -> None:
        _grant_stop.event = stop
        try:
            work()
        except _GrantStopped:
            return
        except Exception as exc:
            box["error"] = exc
        finally:
            _grant_stop.event = None

    thread = threading.Thread(target=run, name="aicad-grant", daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        stop.set()
        _stop_grant_thread(thread)
        raise GrantTimeout(
            f"Granting traverse did not finish within {seconds:g} seconds for {path}. "
            "The sandbox stopped waiting, and that folder was not changed."
        )
    error = box.get("error")
    if error is not None:
        raise error


def _acl_error(prefix: str) -> OSError:
    code = ctypes.get_last_error() & 0xFFFFFFFF
    return OSError(None, f"{prefix} (Windows error {code}).", None, code)


def _security_signatures() -> dict[str, tuple[object, list[object]]]:
    """Return types for the access-list calls. Tests reuse this list."""

    return {
        "GetSecurityInfo": (
            wintypes.DWORD,
            [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            ],
        ),
        "SetKernelObjectSecurity": (
            wintypes.BOOL,
            [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p],
        ),
        "GetAclInformation": (
            wintypes.BOOL,
            [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD],
        ),
        "GetAce": (
            wintypes.BOOL,
            [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)],
        ),
        "InitializeAcl": (
            wintypes.BOOL,
            [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD],
        ),
        "AddAce": (
            wintypes.BOOL,
            [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD],
        ),
        "AddAccessAllowedAce": (
            wintypes.BOOL,
            [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p],
        ),
        "InitializeSecurityDescriptor": (
            wintypes.BOOL,
            [ctypes.c_void_p, wintypes.DWORD],
        ),
        "SetSecurityDescriptorDacl": (
            wintypes.BOOL,
            [ctypes.c_void_p, wintypes.BOOL, ctypes.c_void_p, wintypes.BOOL],
        ),
        "GetLengthSid": (wintypes.DWORD, [ctypes.c_void_p]),
    }


def _bind_security() -> None:
    _bind()
    advapi32 = _advapi32()
    for name, (restype, argtypes) in _security_signatures().items():
        function = getattr(advapi32, name)
        function.argtypes = argtypes
        function.restype = restype


def _acl_information_arguments(
    dacl: ctypes.c_void_p,
    info: _AclSizeInformation,
) -> tuple[object, ...]:
    """Arguments for GetAclInformation. The class is an integer, not the structure."""

    return (dacl, ctypes.byref(info), ctypes.sizeof(info), int(_ACL_SIZE_INFORMATION))


def _open_directory(path: Path, access: int) -> wintypes.HANDLE:
    handle = _kernel32().CreateFileW(
        str(path),
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle is None or ctypes.cast(handle, ctypes.c_void_p).value in {None, _INVALID_HANDLE}:
        raise _acl_error(f"Could not open {path} to grant traverse")
    return handle


def _read_dacl(handle: wintypes.HANDLE) -> tuple[ctypes.c_void_p, ctypes.c_void_p | None]:
    """Return the security descriptor and its DACL.

    A missing DACL already allows access. The caller must free the descriptor.
    The DACL pointer is inside that descriptor.
    """

    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = _advapi32().GetSecurityInfo(
        handle,
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != 0:
        raise OSError(None, f"Could not read the access list (Windows error {status}).", None, status)
    if not dacl.value:
        return descriptor, None
    return descriptor, dacl


def _ace_rows(dacl: ctypes.c_void_p) -> list[tuple[int, int, int, str]]:
    advapi32 = _advapi32()
    info = _AclSizeInformation()
    if not advapi32.GetAclInformation(*_acl_information_arguments(dacl, info)):
        raise _acl_error("Could not read the access entries")
    rows: list[tuple[int, int, int, str]] = []
    for index in range(int(info.AceCount)):
        ace = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
            raise _acl_error("Could not read an access entry")
        header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
        mask = int(ctypes.cast(ace.value + 4, ctypes.POINTER(wintypes.DWORD)).contents.value)
        ace_sid = ""
        if header.AceType == _ACCESS_ALLOWED_ACE_TYPE and int(header.AceSize) >= 8:
            try:
                ace_sid = _sid_to_string(ctypes.c_void_p(ace.value + 8))
            except OSError:
                ace_sid = ""
        rows.append((int(header.AceType), int(header.AceFlags), mask, ace_sid))
    return rows


def _parent_allows_traverse(path: Path, sid: str, package_sid: str) -> bool:
    _bind_security()
    handle = _open_directory(path, _READ_CONTROL)
    descriptor = ctypes.c_void_p()
    try:
        descriptor, dacl = _read_dacl(handle)
        if dacl is None:
            return True
        return not _dacl_needs_grant(_ace_rows(dacl), sid, package_sid)
    finally:
        if descriptor.value:
            _kernel32().LocalFree(descriptor)
        _kernel32().CloseHandle(handle)


def _acl_revision(dacl: ctypes.c_void_p) -> int:
    revision = int(ctypes.cast(dacl, ctypes.POINTER(ctypes.c_ubyte)).contents.value)
    if revision == _ACL_REVISION_DS:
        return _ACL_REVISION_DS
    return _ACL_REVISION


def _append_traverse_ace(dacl: ctypes.c_void_p, sid_ptr: ctypes.c_void_p) -> ctypes.Array:
    advapi32 = _advapi32()
    info = _AclSizeInformation()
    if not advapi32.GetAclInformation(*_acl_information_arguments(dacl, info)):
        raise _acl_error("Could not measure the access list")
    sid_length = int(advapi32.GetLengthSid(sid_ptr))
    if sid_length <= 0:
        raise _acl_error("Could not measure the sandbox id")
    extra = ((8 + sid_length + 3) & ~3) + 8
    total = int(info.AclBytesInUse) + extra
    if total > 65535:
        raise OSError("The access list is too large to add traverse.")
    revision = _acl_revision(dacl)
    buffer = ctypes.create_string_buffer(total)
    if not advapi32.InitializeAcl(buffer, total, revision):
        raise _acl_error("Could not build the access list")
    for index in range(int(info.AceCount)):
        ace = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
            raise _acl_error("Could not copy an access entry")
        size = int(ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents.AceSize)
        # Append. Inserting at the front would reverse the existing entries.
        if not advapi32.AddAce(buffer, revision, 0xFFFFFFFF, ace, size):
            raise _acl_error("Could not copy an access entry")
    if not advapi32.AddAccessAllowedAce(buffer, revision, _TRAVERSE_MASK, sid_ptr):
        raise _acl_error("Could not add traverse")
    return buffer


def _sid_pointer(sid: str) -> ctypes.c_void_p:
    sid_ptr = ctypes.c_void_p()
    if not _advapi32().ConvertStringSidToSidW(sid, ctypes.byref(sid_ptr)):
        raise _acl_error("Could not read the sandbox id")
    return sid_ptr


def _write_traverse_ace(path: Path, sid: str) -> None:
    """Write one non-inherited traverse entry on this directory only.

    SetNamedSecurityInfo walks every child and applies inheritance. Writing
    the open directory handle does not open or change the children.
    """

    _bind_security()
    advapi32 = _advapi32()
    kernel32 = _kernel32()
    sid_ptr = _sid_pointer(sid)
    handle: wintypes.HANDLE | None = None
    descriptor = ctypes.c_void_p()
    try:
        handle = _open_directory(path, _READ_CONTROL | _WRITE_DAC)
        descriptor, dacl = _read_dacl(handle)
        # A missing access list already allows access. Replacing it would lock the folder.
        if dacl is None:
            return
        new_dacl = _append_traverse_ace(dacl, sid_ptr)
        security = ctypes.create_string_buffer(64)
        if not advapi32.InitializeSecurityDescriptor(security, _SECURITY_DESCRIPTOR_REVISION):
            raise _acl_error(f"Could not grant traverse on {path}")
        if not advapi32.SetSecurityDescriptorDacl(security, True, new_dacl, False):
            raise _acl_error(f"Could not grant traverse on {path}")
        if _grant_was_stopped():
            raise _GrantStopped()
        if not advapi32.SetKernelObjectSecurity(handle, _DACL_SECURITY_INFORMATION, security):
            raise _acl_error(f"Could not grant traverse on {path}")
    finally:
        if descriptor.value:
            kernel32.LocalFree(descriptor)
        if handle:
            kernel32.CloseHandle(handle)
        if sid_ptr.value:
            kernel32.LocalFree(sid_ptr)


def _grant_traverse_one(path: Path, sid: str, package_sid: str) -> None:
    if _grant_was_stopped():
        raise _GrantStopped()
    if _parent_allows_traverse(path, sid, package_sid):
        return
    _write_traverse_ace(path, sid)


def _grant_traverse(path: Path, sid: str, package_sid: str | None = None) -> None:
    """Let the container walk down to a folder without granting that folder's children."""

    # The leaf is granted separately. Parents only need traverse. The volume
    # root is not granted: that would be an access entry on the whole drive.
    if not package_sid:
        package_sid = _ALL_APPLICATION_PACKAGES
    if os.name == "nt":
        try:
            text = str(path.resolve())
        except OSError:
            text = str(path)
    else:
        text = str(path)
    for parent in _windows_traverse_parents(text):
        key = (sid, package_sid, ntpath.normcase(parent))
        if key in _granted_traverse:
            continue
        try:
            _finish_within(
                _GRANT_TIMEOUT_SECONDS,
                Path(parent),
                lambda parent=parent: _grant_traverse_one(Path(parent), sid, package_sid),
            )
        except (GrantTimeout, IcaclsTimeout):
            raise
        except OSError as exc:
            if getattr(exc, "winerror", None) in _UNCHANGEABLE_ERRORS:
                continue
            raise
        _granted_traverse.add(key)


def _marker_path() -> Path:
    return Path(sys.prefix) / ".aicad-appcontainer-acl"


# Folders this process has already made readable. Another process's record
# does not stand in for a folder this process has not granted.
_granted_read: set[str] = set()


def grant_read_paths(paths: Sequence[Path], sid: str, package_sid: str | None = None) -> None:
    """Let the container read these folders.

    A folder already granted in this process is not walked again. A record
    left by another process does not skip a folder this process has not granted.
    """

    if not package_sid:
        package_sid = _ALL_APPLICATION_PACKAGES
    for path in paths:
        try:
            key = str(Path(path).resolve())
        except OSError:
            key = os.path.normcase(str(path))
        if key in _granted_read:
            continue
        _grant_tree(Path(path), sid, "(OI)(CI)RX")
        _grant_traverse(Path(path), sid, package_sid)
        _granted_read.add(key)
    marker = _marker_path()
    try:
        from aicad.file_access import read_text

        if read_text(marker, maximum=4096).strip() == sid:
            return
    except OSError:
        pass
    try:
        from aicad.paths import publish_text

        publish_text(marker, sid, root=marker.parent)
    except OSError:
        pass


def _ace_allows_modify(ace_type: int, ace_flags: int, mask: int) -> bool:
    """True when this entry lets new files in the folder be modified.

    The entry has to apply to the folder itself and be inherited by the
    children. An inherit-only entry does not cover the folder.
    """

    if ace_type != _ACCESS_ALLOWED_ACE_TYPE:
        return False
    if ace_flags & _INHERIT_ONLY_ACE:
        return False
    if not (ace_flags & _OBJECT_INHERIT_ACE and ace_flags & _CONTAINER_INHERIT_ACE):
        return False
    if mask & (_GENERIC_ALL | _GENERIC_WRITE):
        return True
    return (mask & _MODIFY_MASK) == _MODIFY_MASK


def _directory_allows_modify(path: Path, sid: str) -> bool:
    """True when ``sid`` can already modify ``path`` and the files created in it."""

    try:
        _bind_security()
        handle = _open_directory(path, _READ_CONTROL)
    except OSError:
        return False
    descriptor = ctypes.c_void_p()
    try:
        descriptor, dacl = _read_dacl(handle)
        if dacl is None:
            return True
        for ace_type, ace_flags, mask, ace_sid in _ace_rows(dacl):
            if ace_sid == sid and _ace_allows_modify(ace_type, ace_flags, mask):
                return True
        return False
    except OSError:
        return False
    finally:
        if descriptor.value:
            _kernel32().LocalFree(descriptor)
        _kernel32().CloseHandle(handle)


def grant_workspace(path: Path, sid: str, package_sid: str | None = None) -> None:
    if not package_sid:
        package_sid = _ALL_APPLICATION_PACKAGES
    path.mkdir(parents=True, exist_ok=True)
    # A design script already granted this folder. Walking it again blocks on
    # files that script has open and can use the whole script time limit.
    if not _directory_allows_modify(path, sid):
        _grant_tree(path, sid, "(OI)(CI)M")
    _grant_traverse(path, sid, package_sid)


_user_sid: str | None = None
_user_sid_checked = False


def _current_user_sid() -> str | None:
    """SID of the person running aicad, so files the container creates stay readable."""

    global _user_sid, _user_sid_checked
    if _user_sid_checked:
        return _user_sid
    _user_sid_checked = True
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "check": False,
        "stdin": subprocess.DEVNULL,
        "timeout": 10,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    try:
        completed = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return _user_sid
    line = (completed.stdout or "").strip()
    if completed.returncode == 0 and "," in line:
        sid = line.rsplit(",", 1)[-1].strip().strip('"')
        if sid.startswith("S-"):
            _user_sid = sid
    return _user_sid


def _reclaim_for_user(path: Path) -> None:
    """Grant the current user access to a tree the AppContainer wrote."""

    if not path.exists():
        return
    sid = _current_user_sid()
    if sid:
        try:
            _icacls(path, sid, "(OI)(CI)F", recurse=True)
            return
        except IcaclsTimeout:
            raise
        except OSError:
            pass
    user = os.environ.get("USERNAME", "").strip()
    if not user:
        return
    _run_icacls(
        ["icacls", str(path), "/grant", f"{user}:(OI)(CI)F", "/T", "/C", "/Q"],
        path,
        recurse=True,
    )


def _create_job(cpu_seconds: int, memory_bytes: int) -> wintypes.HANDLE:
    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(_win_error("Could not create a Job Object"))
    info = _JobExtendedLimit()
    # Breakaway is not set, so a child cannot leave this job.
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_PROCESS_TIME
        | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | _JOB_OBJECT_LIMIT_JOB_MEMORY
        | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    )
    # 100-nanosecond ticks.
    info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_seconds) * 10_000_000
    info.BasicLimitInformation.ActiveProcessLimit = 16
    info.ProcessMemoryLimit = int(memory_bytes)
    info.JobMemoryLimit = int(memory_bytes)
    if not kernel32.SetInformationJobObject(
        job,
        _JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(job)
        raise OSError(_win_error("Could not set Job Object limits"))
    return job


def _open_inherited(path: str, access: int, disposition: int) -> wintypes.HANDLE:
    security = _SecurityAttributes()
    security.nLength = ctypes.sizeof(security)
    security.bInheritHandle = True
    handle = _kernel32().CreateFileW(
        path,
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        ctypes.byref(security),
        disposition,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle is None or ctypes.cast(handle, ctypes.c_void_p).value in {None, _INVALID_HANDLE}:
        raise OSError(_win_error(f"Could not open {path}"))
    return handle


def _environment_block(env: dict[str, str]) -> ctypes.Array:
    items = [f"{key}={value}" for key, value in sorted(env.items())]
    blob = ("\0".join(items) + "\0\0").encode("utf-16-le")
    buffer = ctypes.create_string_buffer(blob)
    return buffer


def _command_for_container(
    command: list[str], env: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    """Run the base interpreter when this process is a virtual-environment redirector.

    The redirector starts another Python and puts that child in a new job.
    The sandbox has to start the interpreter that will run the script.
    ``__PYVENV_LAUNCHER__`` keeps the environment's own prefix.
    """

    copied = dict(env)
    if not command:
        return list(command), copied
    base = getattr(sys, "_base_executable", None) or ""
    current = command[0]
    if not base:
        return list(command), copied
    if os.path.normcase(os.path.normpath(base)) == os.path.normcase(os.path.normpath(current)):
        return list(command), copied
    if os.path.normcase(os.path.normpath(current)) != os.path.normcase(
        os.path.normpath(sys.executable)
    ):
        return list(command), copied
    copied["__PYVENV_LAUNCHER__"] = current
    return [base, *command[1:]], copied


def _creation_flags(*, breakaway: bool) -> int:
    flags = (
        _CREATE_SUSPENDED
        | _CREATE_UNICODE_ENVIRONMENT
        | _CREATE_NO_WINDOW
        | _EXTENDED_STARTUPINFO_PRESENT
    )
    if breakaway:
        flags |= _CREATE_BREAKAWAY_FROM_JOB
    return flags


def _confinement_attributes(
    *,
    sid: ctypes.c_void_p | int,
    job: int,
    handles: tuple[int, int, int],
    lpac: bool,
    network: bool,
    advapi32=None,
) -> tuple[list[tuple[int, int, int]], list[object], ctypes.c_void_p | None]:
    """Attribute rows for CreateProcess. The returned objects keep the buffers alive."""

    alive: list[object] = []
    security = _SecurityCapabilities()
    security.AppContainerSid = sid
    security.CapabilityCount = 0
    security.Capabilities = None
    capability_sid: ctypes.c_void_p | None = None
    if network:
        if advapi32 is None:
            advapi32 = _advapi32()
        capability_sid = ctypes.c_void_p()
        if not advapi32.ConvertStringSidToSidW("S-1-15-3-1", ctypes.byref(capability_sid)):
            raise OSError(_win_error("Could not look up the network capability"))
        capabilities = _SidAndAttributes()
        capabilities.Sid = capability_sid
        capabilities.Attributes = 0x4  # SE_GROUP_ENABLED
        cap_ptr = ctypes.pointer(capabilities)
        security.Capabilities = ctypes.cast(cap_ptr, ctypes.c_void_p)
        security.CapabilityCount = 1
        alive.append(capabilities)
        alive.append(cap_ptr)
    alive.append(security)
    rows = [
        (
            _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
            ctypes.addressof(security),
            ctypes.sizeof(security),
        )
    ]
    job_list = (wintypes.HANDLE * 1)(job)
    alive.append(job_list)
    rows.append(
        (_PROC_THREAD_ATTRIBUTE_JOB_LIST, ctypes.addressof(job_list), ctypes.sizeof(job_list))
    )
    handle_list = (wintypes.HANDLE * 3)(handles[0], handles[1], handles[2])
    alive.append(handle_list)
    rows.append(
        (
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.addressof(handle_list),
            ctypes.sizeof(handle_list),
        )
    )
    if lpac:
        policy = wintypes.DWORD(_PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT)
        alive.append(policy)
        rows.append(
            (
                _PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY,
                ctypes.addressof(policy),
                ctypes.sizeof(policy),
            )
        )
    return rows, alive, capability_sid


def _apply_attributes(kernel32, attribute_list, rows: Sequence[tuple[int, int, int]]) -> None:
    messages = {
        _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES: "Could not attach the AppContainer",
        _PROC_THREAD_ATTRIBUTE_JOB_LIST: "Could not put the script in the Job Object",
        _PROC_THREAD_ATTRIBUTE_HANDLE_LIST: "Could not pass the script's output handles",
        _PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY: (
            "Could not request the stricter AppContainer"
        ),
    }
    for attribute, address, size in rows:
        if not kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            attribute,
            ctypes.c_void_p(address),
            size,
            None,
            None,
        ):
            raise OSError(_win_error(messages.get(attribute, "Could not set a process attribute")))


def _startup_info(attribute_list, stdin: int, stdout: int, stderr: int) -> _StartupInfoEx:
    startup = _StartupInfoEx()
    startup.StartupInfo.cb = ctypes.sizeof(startup)
    startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
    startup.StartupInfo.hStdInput = stdin
    startup.StartupInfo.hStdOutput = stdout
    startup.StartupInfo.hStdError = stderr
    startup.lpAttributeList = ctypes.c_void_p(ctypes.addressof(attribute_list))
    return startup


def _start_suspended(
    kernel32,
    application: str,
    command_line,
    environment,
    cwd: str,
    startup: _StartupInfoEx,
    process: _ProcessInformation,
) -> None:
    """Create the process suspended. Retry without breakaway when the parent job forbids it."""

    last = "Could not start the sandboxed script."
    for breakaway in (True, False):
        created = kernel32.CreateProcessW(
            application,
            command_line,
            None,
            None,
            True,
            _creation_flags(breakaway=breakaway),
            ctypes.cast(environment, ctypes.c_void_p),
            cwd,
            ctypes.byref(startup),
            ctypes.byref(process),
        )
        if created:
            return
        code = _last_error(kernel32)
        last = f"Could not start the sandboxed script (Windows error {code})."
        if breakaway and code == _ERROR_ACCESS_DENIED:
            continue
        raise OSError(last)
    raise OSError(last)


def _process_in_job(kernel32, process: int, job: int) -> bool:
    flag = wintypes.BOOL(False)
    if not kernel32.IsProcessInJob(process, job, ctypes.byref(flag)):
        return False
    return bool(flag.value)


def _process_is_appcontainer(kernel32, advapi32, process: int) -> tuple[bool, str]:
    """Whether this suspended process token is an AppContainer."""

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
        return False, _win_error("Could not read the script token")
    try:
        value = wintypes.DWORD(0)
        returned = wintypes.DWORD(0)
        ok = advapi32.GetTokenInformation(
            token,
            _TokenIsAppContainer,
            ctypes.byref(value),
            ctypes.sizeof(value),
            ctypes.byref(returned),
        )
        if not ok:
            return False, _win_error("Could not read the AppContainer flag")
        if int(value.value) != 1:
            return False, "the process is not an AppContainer"
        return True, ""
    finally:
        if token.value:
            kernel32.CloseHandle(token)


def _commit_suspended(kernel32, advapi32, job: int, process: _ProcessInformation) -> int | None:
    """Put a suspended process in the job, require the container, then resume it.

    A process that is not in the job, or whose token is not an AppContainer,
    is terminated. It is not resumed. The return value is bytes the job had
    already written, or None when that total could not be read.
    """

    assigned = bool(kernel32.AssignProcessToJobObject(job, process.hProcess))
    in_job = assigned or _process_in_job(kernel32, process.hProcess, job)
    reasons: list[str] = []
    if not in_job:
        reasons.append(_win_error("the process is not in the Job Object"))
    else:
        contained, detail = _process_is_appcontainer(kernel32, advapi32, process.hProcess)
        if not contained:
            reasons.append(detail)
    if reasons:
        kernel32.TerminateProcess(process.hProcess, 1)
        raise OSError(
            "Windows did not confine the script ("
            + "; ".join(reasons)
            + "). The script was not started."
        )
    # Read the job before the process runs, so only later writes count.
    baseline = _job_bytes_written(job, kernel32)
    if kernel32.ResumeThread(process.hThread) == 0xFFFFFFFF:
        kernel32.TerminateProcess(process.hProcess, 1)
        raise OSError(_win_error("Could not resume the sandboxed script"))
    return baseline


def _launch(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    job: wintypes.HANDLE,
    sid: ctypes.c_void_p,
    lpac: bool,
    network: bool,
    stdout_path: Path,
    stderr_path: Path,
) -> tuple[_ProcessInformation, int | None]:
    kernel32 = _kernel32()
    advapi32 = _advapi32()
    command, env = _command_for_container(command, env)
    stdin = _open_inherited("NUL", _GENERIC_READ, _OPEN_EXISTING)
    stdout = _open_inherited(str(stdout_path), _GENERIC_WRITE, _CREATE_ALWAYS)
    stderr = _open_inherited(str(stderr_path), _GENERIC_WRITE, _CREATE_ALWAYS)
    capability_sid = None
    try:
        # internetClient is added only when the script was given the network.
        rows, alive, capability_sid = _confinement_attributes(
            sid=sid,
            job=job,
            handles=(stdin, stdout, stderr),
            lpac=lpac,
            network=network,
            advapi32=advapi32,
        )
        attribute_count = len(rows)
        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, attribute_count, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(
            attributes, attribute_count, 0, ctypes.byref(size)
        ):
            raise OSError(_win_error("Could not prepare the process attributes"))
        try:
            _apply_attributes(kernel32, attributes, rows)
            startup = _startup_info(attributes, stdin, stdout, stderr)
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            environment = _environment_block(env)
            process = _ProcessInformation()
            _start_suspended(
                kernel32,
                command[0],
                command_line,
                environment,
                cwd,
                startup,
                process,
            )
            try:
                baseline = _commit_suspended(kernel32, advapi32, job, process)
            except Exception:
                kernel32.CloseHandle(process.hThread)
                kernel32.CloseHandle(process.hProcess)
                raise
            return process, baseline
        finally:
            # CreateProcess has copied the attribute pointers. The buffers in
            # ``alive`` stay reachable until this function returns.
            del alive
            kernel32.DeleteProcThreadAttributeList(attributes)
    finally:
        kernel32.CloseHandle(stdin)
        kernel32.CloseHandle(stdout)
        kernel32.CloseHandle(stderr)
        if capability_sid:
            kernel32.LocalFree(capability_sid)


def _read_text(path: Path) -> str:
    """The end of a script output file. A longer file is not read in full."""
    try:
        from aicad.file_access import read_tail
        from aicad.sandbox import _SCRIPT_OUTPUT_CAP

        data = read_tail(path, _SCRIPT_OUTPUT_CAP)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


class _JobBasicAndIo(ctypes.Structure):
    """JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION."""

    _fields_ = [
        ("BasicInfo", _JobBasicAccounting),
        ("IoInfo", _IoCounters),
    ]


# JobObjectBasicAndIoAccountingInformation. WriteTransferCount is the job's writes.
_JobObjectBasicAndIoAccountingInformation = 8


def _job_bytes_written(job: wintypes.HANDLE, kernel32=None) -> int | None:
    kernel32 = _kernel32() if kernel32 is None else kernel32
    query = getattr(kernel32, "QueryInformationJobObject", None)
    if query is None:
        return None
    info = _JobBasicAndIo()
    returned = wintypes.DWORD(0)
    if not query(
        job,
        _JobObjectBasicAndIoAccountingInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(returned),
    ):
        return None
    return int(info.IoInfo.WriteTransferCount)


def _account_job_io(disk, job: wintypes.HANDLE, baseline: int | None) -> None:
    """Count the job's writes so far.

    The job keeps that total after the process exits, until the job handle is closed.
    """

    if disk is None:
        return
    written = _job_bytes_written(job)
    if written is None:
        return
    disk.note_job_io(written, baseline)


def _job_memory_killed(job: wintypes.HANDLE, memory_bytes: int) -> bool:
    info = _JobExtendedLimit()
    returned = wintypes.DWORD(0)
    if not _kernel32().QueryInformationJobObject(
        job,
        _JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(returned),
    ):
        return False
    peak = max(int(info.PeakProcessMemoryUsed), int(info.PeakJobMemoryUsed))
    return peak >= int(memory_bytes * 0.85)


# JobObjectLimitViolationInformation. Memory bits show up here. The CPU-time
# cap does not, so user-time accounting is checked separately.
_JobObjectLimitViolationInformation = 13
_LIMIT_VIOLATION_BITS = (
    _JOB_OBJECT_LIMIT_PROCESS_TIME
    | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
    | _JOB_OBJECT_LIMIT_JOB_MEMORY
)


class _JobLimitViolation(ctypes.Structure):
    _fields_ = [
        ("LimitFlags", wintypes.DWORD),
        ("ViolationLimitFlags", wintypes.DWORD),
        ("IoReadBytes", ctypes.c_uint64),
        ("IoReadBytesLimit", ctypes.c_uint64),
        ("IoWriteBytes", ctypes.c_uint64),
        ("IoWriteBytesLimit", ctypes.c_uint64),
        ("IoOtherBytes", ctypes.c_uint64),
        ("IoOtherBytesLimit", ctypes.c_uint64),
        ("IoReadOperations", ctypes.c_uint64),
        ("IoReadOperationsLimit", ctypes.c_uint64),
        ("IoWriteOperations", ctypes.c_uint64),
        ("IoWriteOperationsLimit", ctypes.c_uint64),
        ("IoOtherOperations", ctypes.c_uint64),
        ("IoOtherOperationsLimit", ctypes.c_uint64),
        ("JobMemory", ctypes.c_uint64),
        ("JobMemoryLimit", ctypes.c_uint64),
        ("RateControlTolerance", wintypes.DWORD),
        ("RateControlToleranceLimit", wintypes.DWORD),
    ]


def _user_time_reached_limit(total_user_ticks: int, limit_ticks: int) -> bool:
    """True when user time is within 100 ms of the per-process cap.

    The recorded time can stop a little short of the cap. A cap shorter than
    that slack has to be met exactly, so a short crash is not a CPU stop.
    """

    limit = int(limit_ticks)
    if limit <= 0:
        return False
    threshold = limit - _CPU_LIMIT_SLACK_TICKS
    if threshold < 0:
        threshold = limit
    return int(total_user_ticks) >= threshold


def _job_cpu_time_reached(job: wintypes.HANDLE) -> bool:
    """True when this job's user time has reached its per-process cap.

    The violation record does not include the CPU-time bit, and the process
    exit can look ordinary. The accounting counter is the record of that cap.
    """

    kernel32 = _kernel32()
    accounting = _JobBasicAccounting()
    returned = wintypes.DWORD(0)
    if not kernel32.QueryInformationJobObject(
        job,
        _JobObjectBasicAccountingInformation,
        ctypes.byref(accounting),
        ctypes.sizeof(accounting),
        ctypes.byref(returned),
    ):
        return False
    limits = _JobExtendedLimit()
    if not kernel32.QueryInformationJobObject(
        job,
        _JobObjectExtendedLimitInformation,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
        ctypes.byref(returned),
    ):
        return False
    flags = int(limits.BasicLimitInformation.LimitFlags)
    if not flags & _JOB_OBJECT_LIMIT_PROCESS_TIME:
        return False
    return _user_time_reached_limit(
        int(accounting.TotalUserTime),
        int(limits.BasicLimitInformation.PerProcessUserTimeLimit),
    )


def _job_stop_was_a_limit(
    *,
    timed_out: bool,
    violated: bool,
    cpu_reached: bool,
    exit_code: int,
    memory_peak: bool,
) -> bool:
    """True when the job's CPU or memory cap stopped the script.

    A wall-clock timeout is separate. User time at the cap counts even when
    the exit code is 0. A memory peak counts only on a failed exit, so a
    script that finishes is not reported as a memory stop.
    """

    if timed_out:
        return False
    if violated or cpu_reached:
        return True
    return exit_code != 0 and memory_peak


def _job_limit_violated(job: wintypes.HANDLE) -> bool:
    """True when the job reports a CPU or memory limit violation."""
    info = _JobLimitViolation()
    returned = wintypes.DWORD(0)
    if not _kernel32().QueryInformationJobObject(
        job,
        _JobObjectLimitViolationInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(returned),
    ):
        return False
    return bool(int(info.ViolationLimitFlags) & _LIMIT_VIOLATION_BITS)


# ALE authorize layers. No protocol field and no port field: one package SID
# matches TCP, UDP, and every other protocol those layers classify.
_FWP_ACTION_BLOCK = 0x00001001
_FWP_UINT64 = 4
_FWP_SID = 13
_FWP_MATCH_EQUAL = 0
_FWPM_FILTER_FLAG_PERSISTENT = 0x00000001
_FWPM_FILTER_FLAG_CLEAR_ACTION_RIGHT = 0x00000008
_FWPM_SESSION_FLAG_DYNAMIC = 0x00000001
_RPC_C_AUTHN_WINNT = 10
_WFP_ALE_PACKAGE_ID = "71bc78fa-f17c-4997-a602-6abb261f351c"
_WFP_BLOCK_LAYERS = (
    ("ALE_AUTH_CONNECT_V4", "c38d57d1-05a7-4c33-904f-7fbceee60e82"),
    ("ALE_AUTH_CONNECT_V6", "4a72393b-319f-44bc-84c3-ba54dcb3b6b4"),
    ("ALE_AUTH_RECV_ACCEPT_V4", "e1cd9fe7-f4b5-4273-96c0-592e487b8650"),
    ("ALE_AUTH_RECV_ACCEPT_V6", "a3b42c97-9f04-4672-b87e-cee9c483257f"),
    ("ALE_AUTH_LISTEN_V4", "88bb5dad-76d7-4227-9c71-df0a3ed7be7e"),
    ("ALE_AUTH_LISTEN_V6", "7ac9de24-17dd-4814-b4bd-a9fbc95a321b"),
    ("ALE_RESOURCE_ASSIGNMENT_V4", "1247d66d-0b60-4a15-8d44-7155d0f53a0c"),
    ("ALE_RESOURCE_ASSIGNMENT_V6", "55a650e1-5f0a-4eca-a653-88f53b26aa8c"),
)
_fwpuclnt_lib: ctypes.WinDLL | None = None
_wfp_engine: int | None = None


class _Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _WfpDisplay(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_wchar_p),
        ("description", ctypes.c_wchar_p),
    ]


class _WfpBlob(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _WfpValueData(ctypes.Union):
    _fields_ = [
        ("uint8", ctypes.c_uint8),
        ("uint64", ctypes.POINTER(ctypes.c_uint64)),
        ("sid", ctypes.c_void_p),
    ]


class _WfpValue(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("data", _WfpValueData),
    ]


class _WfpCondition(ctypes.Structure):
    _fields_ = [
        ("fieldKey", _Guid),
        ("matchType", ctypes.c_uint32),
        ("value", _WfpValue),
    ]


class _WfpActionData(ctypes.Union):
    _fields_ = [
        ("filterType", _Guid),
        ("calloutKey", _Guid),
    ]


class _WfpAction(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("data", _WfpActionData),
    ]


class _WfpContext(ctypes.Union):
    _fields_ = [
        ("rawContext", ctypes.c_uint64),
        ("providerContextKey", _Guid),
    ]


class _WfpFilter(ctypes.Structure):
    """FWPM_FILTER0. The layout is the 64-bit one: 200 bytes."""

    _fields_ = [
        ("filterKey", _Guid),
        ("displayData", _WfpDisplay),
        ("flags", ctypes.c_uint32),
        ("providerKey", ctypes.c_void_p),
        ("providerData", _WfpBlob),
        ("layerKey", _Guid),
        ("subLayerKey", _Guid),
        ("weight", _WfpValue),
        ("numFilterConditions", ctypes.c_uint32),
        ("filterCondition", ctypes.POINTER(_WfpCondition)),
        ("action", _WfpAction),
        ("context", _WfpContext),
        ("reserved", ctypes.c_void_p),
        ("filterId", ctypes.c_uint64),
        ("effectiveWeight", _WfpValue),
    ]


class _WfpSession(ctypes.Structure):
    _fields_ = [
        ("sessionKey", _Guid),
        ("displayData", _WfpDisplay),
        ("flags", ctypes.c_uint32),
        ("txnWaitTimeoutInMSec", ctypes.c_uint32),
        ("processId", ctypes.c_uint32),
        ("sid", ctypes.c_void_p),
        ("username", ctypes.c_wchar_p),
        ("kernelMode", ctypes.c_int),
    ]


class _WfpSubLayer(ctypes.Structure):
    _fields_ = [
        ("subLayerKey", _Guid),
        ("displayData", _WfpDisplay),
        ("flags", ctypes.c_uint32),
        ("providerKey", ctypes.c_void_p),
        ("providerData", _WfpBlob),
        ("weight", ctypes.c_uint16),
    ]


def _guid(text: str) -> _Guid:
    return _Guid.from_buffer_copy(uuid.UUID(text).bytes_le)


def _wfp_failure(step: str, code: int) -> OSError:
    number = code & 0xFFFFFFFF
    return OSError(f"Could not set the script network block ({step}, code {number:#010x}).")


def _wfp_block_filter(layer: str, sid_pointer: int, sublayer: _Guid) -> _WfpFilter:
    """One hard block for this container. The only condition is its package SID."""

    if not sid_pointer:
        raise OSError("Could not set the script network block (the container id was empty).")
    weight = ctypes.c_uint64(0xFFFFFFFFFFFFFFFF)
    condition = _WfpCondition()
    condition.fieldKey = _guid(_WFP_ALE_PACKAGE_ID)
    condition.matchType = _FWP_MATCH_EQUAL
    condition.value.type = _FWP_SID
    condition.value.data.sid = sid_pointer
    name = ctypes.create_unicode_buffer("aicad script network block")
    description = ctypes.create_unicode_buffer("Blocks one aicad script container.")
    filt = _WfpFilter()
    filt.displayData.name = ctypes.cast(name, ctypes.c_wchar_p)
    filt.displayData.description = ctypes.cast(description, ctypes.c_wchar_p)
    filt.flags = _FWPM_FILTER_FLAG_CLEAR_ACTION_RIGHT
    filt.layerKey = _guid(layer)
    filt.subLayerKey = sublayer
    filt.weight.type = _FWP_UINT64
    filt.weight.data.uint64 = ctypes.pointer(weight)
    filt.numFilterConditions = 1
    filt.filterCondition = ctypes.pointer(condition)
    filt.action.type = _FWP_ACTION_BLOCK
    filt._keepalive = [weight, condition, name, description]
    return filt


def _wfp_block_filters(sid_pointer: int, sublayer: _Guid | None = None) -> list[_WfpFilter]:
    key = sublayer if sublayer is not None else _guid(str(uuid.uuid4()))
    return [_wfp_block_filter(layer, sid_pointer, key) for _name, layer in _WFP_BLOCK_LAYERS]


def _fwpuclnt() -> ctypes.WinDLL:
    global _fwpuclnt_lib
    if _fwpuclnt_lib is None:
        library = ctypes.WinDLL("fwpuclnt", use_last_error=True)
        library.FwpmEngineOpen0.argtypes = [
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(_WfpSession),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.FwpmEngineOpen0.restype = wintypes.DWORD
        library.FwpmEngineClose0.argtypes = [ctypes.c_void_p]
        library.FwpmEngineClose0.restype = wintypes.DWORD
        library.FwpmSubLayerAdd0.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WfpSubLayer),
            ctypes.c_void_p,
        ]
        library.FwpmSubLayerAdd0.restype = wintypes.DWORD
        library.FwpmFilterAdd0.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WfpFilter),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint64),
        ]
        library.FwpmFilterAdd0.restype = wintypes.DWORD
        _fwpuclnt_lib = library
    return _fwpuclnt_lib


def _install_wfp_block(sid_text: str) -> None:
    """Block this container on every ALE authorize layer. Fail closed."""

    global _wfp_engine
    if sys.platform != "win32":
        return
    if _wfp_engine:
        raise OSError("Could not set the script network block (a filter is already installed).")
    _bind()
    client = _fwpuclnt()
    session_name = ctypes.create_unicode_buffer("aicad script network")
    session_text = ctypes.create_unicode_buffer("Blocks one aicad script container.")
    session = _WfpSession()
    session.flags = _FWPM_SESSION_FLAG_DYNAMIC
    session.displayData.name = ctypes.cast(session_name, ctypes.c_wchar_p)
    session.displayData.description = ctypes.cast(session_text, ctypes.c_wchar_p)
    session.kernelMode = 0
    engine = ctypes.c_void_p()
    code = int(
        client.FwpmEngineOpen0(
            None,
            _RPC_C_AUTHN_WINNT,
            None,
            ctypes.byref(session),
            ctypes.byref(engine),
        )
    )
    if code or not engine.value:
        raise _wfp_failure("FwpmEngineOpen0", code)
    sid_ptr = None
    try:
        sid_ptr = _sid_pointer(sid_text)
        if not sid_ptr.value:
            raise OSError("Could not set the script network block (the container id was empty).")
        sublayer_key = _guid(str(uuid.uuid4()))
        sublayer_name = ctypes.create_unicode_buffer("aicad script network")
        sublayer_text = ctypes.create_unicode_buffer("Blocks one aicad script container.")
        sublayer = _WfpSubLayer()
        sublayer.subLayerKey = sublayer_key
        sublayer.displayData.name = ctypes.cast(sublayer_name, ctypes.c_wchar_p)
        sublayer.displayData.description = ctypes.cast(sublayer_text, ctypes.c_wchar_p)
        sublayer.weight = 0xFFFF
        code = int(client.FwpmSubLayerAdd0(engine, ctypes.byref(sublayer), None))
        if code:
            raise _wfp_failure("FwpmSubLayerAdd0", code)
        for name, layer in _WFP_BLOCK_LAYERS:
            filt = _wfp_block_filter(layer, int(sid_ptr.value), sublayer_key)
            if int(filt.numFilterConditions) != 1 or int(filt.action.type) != _FWP_ACTION_BLOCK:
                raise OSError(
                    "Could not set the script network block (the filter is not limited to the container)."
                )
            added = ctypes.c_uint64()
            code = int(client.FwpmFilterAdd0(engine, ctypes.byref(filt), None, ctypes.byref(added)))
            if code:
                raise _wfp_failure(name, code)
    except Exception:
        client.FwpmEngineClose0(engine)
        raise
    finally:
        if sid_ptr:
            _kernel32().LocalFree(sid_ptr)
    _wfp_engine = int(engine.value)


def _close_wfp_block() -> None:
    """Drop the dynamic filters. They also die when this process exits."""

    global _wfp_engine
    engine = _wfp_engine
    _wfp_engine = None
    if not engine or sys.platform != "win32":
        return
    _fwpuclnt().FwpmEngineClose0(ctypes.c_void_p(engine))


def _firewall_rule_names() -> tuple[str, ...]:
    return _NETWORK_OFF_RULES + _NETWORK_OFF_STALE_RULES


def _firewall_script(sid: str | None = None, *, install: bool) -> str:
    """PowerShell that blocks, or clears, this container's inbound and outbound traffic.

    The package id is the AppContainer SID. A capability SID is not accepted.
    Every protocol is blocked. The rules name no port.
    """

    names = ", ".join(repr(name) for name in _firewall_rule_names())
    # A missing rule is not an error. Ending on that lookup would make
    # powershell.exe exit 1, and a later script that needs the network
    # would not start.
    remove = (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        f"  foreach ($name in @({names})) {{\n"
        "    foreach ($rule in @(Get-NetFirewallRule -Name $name -ErrorAction Ignore)) {\n"
        "      Remove-NetFirewallRule -InputObject $rule\n"
        "    }\n"
        "  }\n"
    )
    if not install:
        return remove + "} catch {\n  Write-Output ($_ | Out-String)\n  exit 1\n}\nexit 0\n"
    if sid is None or _PACKAGE_SID_TEXT.fullmatch(sid) is None:
        raise OSError("The AppContainer id is not a package SID, so the network was not blocked.")
    package = sid.upper()
    # Remove returns before the firewall service drops the name. Wait until
    # the name is gone, then create the block. The script has not started.
    fresh = ", ".join(repr(name) for name in _NETWORK_OFF_RULES)
    wait = (
        f"  foreach ($name in @({fresh})) {{\n"
        "    $gone = $false\n"
        "    foreach ($attempt in 1..40) {\n"
        "      $left = @(Get-NetFirewallRule -Name $name -ErrorAction Ignore)\n"
        "      if ($left.Count -eq 0) { $gone = $true; break }\n"
        "      Start-Sleep -Milliseconds 250\n"
        "    }\n"
        "    if (-not $gone) { throw 'the network block name is still registered' }\n"
        "  }\n"
    )
    return (
        remove
        + wait
        + "  New-NetFirewallRule -Name 'aicad-script-network-off-out' "
        + "-DisplayName 'aicad script network off outbound' "
        + "-Direction Outbound -Action Block -Protocol Any "
        + f"-Package '{package}' -Profile Any -Enabled True | Out-Null\n"
        + "  New-NetFirewallRule -Name 'aicad-script-network-off-in' "
        + "-DisplayName 'aicad script network off inbound' "
        + "-Direction Inbound -Action Block -Protocol Any "
        + f"-Package '{package}' -Profile Any -Enabled True | Out-Null\n"
        + "} catch {\n  Write-Output ($_ | Out-String)\n  exit 1\n}\nexit 0\n"
    )


def _powershell_executable() -> str:
    """The system PowerShell, not a name looked up on PATH.

    Windows PowerShell is under System32\\WindowsPowerShell, not in System32
    itself. A child whose PATH is only the interpreter and the system
    directories still has to install the network block.
    """

    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not root:
        return "powershell.exe"
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _run_firewall_script(script: str) -> None:
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        # Later calls get slower. A timeout is still a missing block.
        "timeout": 120,
        "check": False,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            [_powershell_executable(), "-NoProfile", "-NonInteractive", "-Command", script],
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError(f"Could not set the script network block. {exc}") from exc
    if completed.returncode != 0:
        detail = ((completed.stderr or "") + (completed.stdout or "")).strip()
        raise OSError(
            "Could not set the script network block "
            f"(code {completed.returncode}). {detail}"
        )


def _lookup_profile_local_app_data() -> Path | None:
    """The real local profile directory, not TEMP and not an overridden variable.

    A sandboxed script's environment points this name at the project, and the
    project is a folder it can write. The known profile folder is neither.
    """

    if sys.platform != "win32":
        return Path.home() / ".config"
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    except OSError:
        return None
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    folder = _guid(_FOLDERID_LOCAL_APPDATA)
    found = ctypes.c_void_p()
    code = int(shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(found)))
    if code != 0 or not found.value:
        return None
    try:
        text = ctypes.wstring_at(found.value)
    finally:
        ole32.CoTaskMemFree(found)
    if not text:
        return None
    return Path(text)


def _profile_local_app_data() -> Path | None:
    global _profile_local_app_data_known, _profile_local_app_data_path
    if _profile_local_app_data_known:
        return _profile_local_app_data_path
    _profile_local_app_data_known = True
    _profile_local_app_data_path = _lookup_profile_local_app_data()
    return _profile_local_app_data_path


def _network_off_marker() -> Path | None:
    if _network_off_marker_path is not None:
        return _network_off_marker_path
    base = _profile_local_app_data()
    if base is None:
        return None
    return base / "aicad" / "script-network-off-sid"


def _recorded_rules_sid() -> str | None:
    path = _network_off_marker()
    if path is None:
        return None
    try:
        from aicad.file_access import read_text

        text = read_text(path, maximum=4096).strip()
    except OSError:
        return None
    if _PACKAGE_SID_TEXT.fullmatch(text) is None:
        return None
    return text.upper()


def _record_rules_sid(sid: str) -> None:
    path = _network_off_marker()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from aicad.paths import publish_text

        publish_text(path, sid.upper() + "\n", root=path.parent)
    except OSError:
        return


def _forget_recorded_rules() -> None:
    path = _network_off_marker()
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
    _forget_firewall_verified()


def _firewall_verified_path() -> Path | None:
    try:
        marker = _network_off_marker()
    except (OSError, AttributeError):
        return None
    if marker is None:
        return None
    return marker.with_name("script-network-off-verified")


def _remember_firewall_verified(sid: str) -> None:
    """Remember a successful read-back while this process is still running."""

    import time

    path = _firewall_verified_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{sid.upper()} {os.getpid()} {time.time():.0f}\n", encoding="utf-8")
    except OSError:
        return


def _firewall_recently_verified(sid: str) -> bool:
    """True when a live process read this block back within the script limit.

    A part script started from a design script would otherwise read the
    firewall again. That read can take as long as the design script is
    allowed to run.
    """

    path = _firewall_verified_path()
    if path is None or _PACKAGE_SID_TEXT.fullmatch(sid) is None:
        return False
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if len(text) > 4096:
        return False
    parts = text.split()
    if len(parts) != 3 or parts[0].upper() != sid.upper():
        return False
    if _PACKAGE_SID_TEXT.fullmatch(parts[0]) is None:
        return False
    try:
        pid = int(parts[1])
        when = float(parts[2])
    except ValueError:
        return False
    if not _pid_alive(pid):
        return False
    import time

    return time.time() - when <= 120


def _forget_firewall_verified() -> None:
    path = _firewall_verified_path()
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _firewall_present_script(sid: str) -> str:
    """Read the two block rules. This script does not create or remove any rule."""

    if _PACKAGE_SID_TEXT.fullmatch(sid) is None:
        raise OSError("The AppContainer id is not a package SID, so the network was not blocked.")
    package = sid.upper()
    names = (
        ("aicad-script-network-off-out", "Outbound"),
        ("aicad-script-network-off-in", "Inbound"),
    )
    checks = "\n".join(
        f"  if (-not (Test-AicadBlock '{name}' '{direction}' '{package}')) {{ exit 2 }}\n"
        for name, direction in names
    )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "function Test-AicadBlock([string]$Name, [string]$Direction, [string]$Package) {\n"
        "  $rules = @(Get-NetFirewallRule -Name $Name -ErrorAction Ignore)\n"
        "  if ($rules.Count -ne 1) { return $false }\n"
        "  $rule = $rules[0]\n"
        "  if ([string]$rule.Enabled -ne 'True') { return $false }\n"
        "  if ([string]$rule.Action -ne 'Block') { return $false }\n"
        "  if ([string]$rule.Direction -ne $Direction) { return $false }\n"
        "  $ports = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule)\n"
        "  if ($ports.Count -ne 1) { return $false }\n"
        "  $protocol = [string]$ports[0].Protocol\n"
        "  if ($protocol -ne 'Any' -and $protocol -ne '256') { return $false }\n"
        "  foreach ($value in @($ports[0].LocalPort) + @($ports[0].RemotePort)) {\n"
        "    foreach ($item in @($value)) {\n"
        "      $text = [string]$item\n"
        "      if ($text -and $text -ne 'Any') { return $false }\n"
        "    }\n"
        "  }\n"
        "  $apps = @(Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule)\n"
        "  if ($apps.Count -ne 1) { return $false }\n"
        "  if ([string]$apps[0].Package.Trim().ToUpperInvariant() -ne $Package) { return $false }\n"
        "  return $true\n"
        "}\n"
        "try {\n"
        f"{checks}"
        "  exit 0\n"
        "} catch {\n"
        "  Write-Output ($_ | Out-String)\n"
        "  exit 2\n"
        "}\n"
    )


def _firewall_block_is_present(sid: str) -> bool:
    """True only when both block rules for this package are installed now.

    A missing rule, a timed-out lookup, or any other failure is not present.
    The caller installs the rules again or does not start the script. A live
    process that just read the same block back is reused, so the lookup does
    not run again inside that script's time limit.
    """

    if sys.platform != "win32":
        return False
    if _firewall_recently_verified(sid):
        return True
    try:
        _run_firewall_script(_firewall_present_script(sid))
    except OSError:
        return False
    _remember_firewall_verified(sid)
    return True


def _hold_network_block(sid_text: str) -> None:
    """Install the container block before a network-off script starts.

    A block the read-back already accepts is left in place. A missing record
    does not remove it. A missing block is installed, and this returns only
    after the read-back accepts it. The filter is installed for each hold,
    because closing it drops the dynamic session.
    """

    global _network_off_holders, _network_off_sid, _network_off_rules_sid
    with _network_off_lock:
        wanted = sid_text.upper()
        if _network_off_holders and _network_off_sid != wanted:
            raise OSError(
                "A script still has the network blocked, so this script was not started."
            )
        if _network_off_holders == 0:
            if not _firewall_block_is_present(wanted):
                _run_firewall_script(_firewall_script(sid_text, install=True))
                if not _firewall_block_is_present(wanted):
                    raise OSError(
                        "The script network block is not in place, so the script was not started."
                    )
            if _recorded_rules_sid() != wanted:
                _record_rules_sid(wanted)
            _network_off_rules_sid = wanted
            try:
                if _wfp_engine is None:
                    _install_wfp_block(sid_text)
            except Exception:
                try:
                    _run_firewall_script(_firewall_script(install=False))
                except OSError:
                    pass
                _forget_recorded_rules()
                _network_off_rules_sid = None
                raise
            _network_off_sid = wanted
        _network_off_holders += 1


def _release_network_block() -> None:
    """Drop the filter once the last network-off child has exited.

    The firewall rules stay. The next network-off script reads them back and
    leaves a block that already matches this package. A network-on script
    removes them before it starts.
    """

    global _network_off_holders, _network_off_sid
    with _network_off_lock:
        if _network_off_holders <= 0:
            return
        _network_off_holders -= 1
        if _network_off_holders > 0:
            return
        _network_off_sid = None
        _close_wfp_block()


def _require_script_network_open() -> None:
    """A network-on script must not run while the shared container is blocked."""

    global _network_off_sid, _network_off_rules_sid
    with _network_off_lock:
        if _network_off_holders:
            raise OSError(
                "A script still has the network blocked, so this script was not started."
            )
        _close_wfp_block()
        _forget_recorded_rules()
        _run_firewall_script(_firewall_script(install=False))
        _network_off_sid = None
        _network_off_rules_sid = None


def spawn(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    read_paths: Sequence[Path],
    workspace: Path,
    cpu_seconds: int,
    memory_bytes: int,
    network: bool,
    timeout_seconds: float,
    stdout_path: Path,
    stderr_path: Path,
    disk=None,
) -> tuple[int, str, str, bool, bool, str]:
    """Start one confined process. Returns code, stdout, stderr, timed_out, memory_killed, mechanism."""

    _bind()
    mode = ensure_mode(read_paths)
    sid = None
    job = None
    process = None
    held_block = False
    try:
        sid, sid_text = _appcontainer_sid()
        # Do not rewrite this process's access list. An AppContainer can open
        # a process only when that process grants the container, and this
        # process does not. Adding a deny entry here stopped later scripts
        # from finishing in CI. See SECURITY.md.
        package = _package_sid(mode == "lpac")
        grant_workspace(workspace, sid_text, package)
        grant_read_paths(read_paths, sid_text, package)
        job = _create_job(cpu_seconds, memory_bytes)
        if network:
            _require_script_network_open()
        else:
            # The block is in place before the process is resumed. The child
            # then checks that the connection is refused.
            _hold_network_block(sid_text)
            held_block = True
        process, io_base = _launch(
            command,
            cwd=cwd,
            env=env,
            job=job,
            sid=sid,
            lpac=mode == "lpac",
            network=network,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        if io_base is None:
            io_base = _job_bytes_written(job)
        wait_ms = max(1, int(timeout_seconds * 1000))
        spent = 0
        finished = False
        # The directory watch records a file as it appears. This wait is how
        # soon the job stops after that cap is hit.
        while spent < wait_ms:
            step = min(20, wait_ms - spent)
            waited = _kernel32().WaitForSingleObject(process.hProcess, step)
            if waited != _WAIT_TIMEOUT:
                finished = True
                break
            spent += step
            _account_job_io(disk, job, io_base)
            if disk is not None and disk.hit.is_set():
                _kernel32().TerminateJobObject(job, 1)
                _kernel32().WaitForSingleObject(process.hProcess, 5000)
                break
        else:
            finished = False
        timed_out = not finished and not (disk is not None and disk.hit.is_set())
        if timed_out:
            _kernel32().TerminateJobObject(job, 1)
            _kernel32().WaitForSingleObject(process.hProcess, 5000)
        # The process may already have exited. The job still has the write total.
        _account_job_io(disk, job, io_base)
        code = wintypes.DWORD(0)
        _kernel32().GetExitCodeProcess(process.hProcess, ctypes.byref(code))
        # The stored code is unsigned. A kill shows up above the normal exit range.
        exit_code = int(code.value)
        if exit_code >= 0x80000000:
            exit_code = exit_code - 0x100000000
        # A CPU-time kill often leaves an ordinary exit code, and the
        # violation record does not include that limit. User time at the cap
        # is what says the limit stopped the script.
        memory_killed = _job_stop_was_a_limit(
            timed_out=timed_out,
            violated=_job_limit_violated(job),
            cpu_reached=_job_cpu_time_reached(job),
            exit_code=exit_code,
            memory_peak=_job_memory_killed(job, memory_bytes),
        )
        # Files the container creates can omit this user's inherited access.
        # Give it back before the parent reads the result.
        _reclaim_for_user(stdout_path.parent)
        cache_home = env.get("XDG_CACHE_HOME", "").strip()
        if cache_home:
            _reclaim_for_user(Path(cache_home))
        mechanism = "lpac+job" if mode == "lpac" else "appcontainer+job"
        return (
            exit_code,
            _read_text(stdout_path),
            _read_text(stderr_path),
            timed_out,
            memory_killed,
            mechanism,
        )
    finally:
        try:
            # Terminate the job on the way out, not only on the wall-clock timeout,
            # so a child the script started does not keep running after the tool returns.
            if job is not None:
                _kernel32().TerminateJobObject(job, 1)
            if process is not None:
                _kernel32().CloseHandle(process.hThread)
                _kernel32().CloseHandle(process.hProcess)
            if job is not None:
                _kernel32().CloseHandle(job)
            if held_block:
                _release_network_block()
            if sid is not None:
                _advapi32().FreeSid(sid)
        finally:
            if sid is not None:
                _release_profile_use()


def _probe_source() -> str:
    """Probe program. A process that only prints is not confined."""

    return (
        "import aicad\n"
        "from aicad.sandbox_windows import identity_failures\n"
        f"failed = identity_failures({_PROBE_MEMORY_BYTES})\n"
        "if failed:\n"
        "    raise SystemExit('confinement: ' + '; '.join(failed))\n"
        "print('sandbox-ok')\n"
    )


def _probe_command(workspace: Path) -> list[str]:
    script = workspace / "probe.py"
    # The real script imports aicad. A probe that only prints can succeed in a
    # container that then fails to load the geometry libraries. The same
    # confinement check the script uses has to pass before this counts.
    from aicad.paths import publish_text

    publish_text(script, _probe_source(), root=script.parent)
    # -P keeps the project folder, which is both the working directory and the
    # script directory, off sys.path. The script path is absolute.
    return [sys.executable, "-P", "-B", "-u", str(script.resolve())]


def _probe_mode(mode: str, read_paths: Sequence[Path]) -> str | None:
    """Return an error string when this AppContainer mode cannot start Python."""

    import tempfile

    _bind()
    root = Path(tempfile.mkdtemp(prefix="aicad-win-probe-"))
    sid = None
    try:
        sid, sid_text = _appcontainer_sid()
        try:
            package = _package_sid(mode == "lpac")
            grant_read_paths(read_paths, sid_text, package)
            grant_workspace(root, sid_text, package)
            job = _create_job(_PROBE_CPU_SECONDS, _PROBE_MEMORY_BYTES)
            held_block = False
            try:
                stdout_path = root / "stdout.txt"
                stderr_path = root / "stderr.txt"
                _hold_network_block(sid_text)
                held_block = True
                process, _io_base = _launch(
                    _probe_command(root),
                    cwd=str(root),
                    env=_probe_env(root),
                    job=job,
                    sid=sid,
                    lpac=mode == "lpac",
                    network=False,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )
                try:
                    _kernel32().WaitForSingleObject(process.hProcess, 60_000)
                    code = wintypes.DWORD(0)
                    _kernel32().GetExitCodeProcess(process.hProcess, ctypes.byref(code))
                    output = _read_text(stdout_path)
                    if code.value == 0 and "sandbox-ok" in output:
                        return None
                    detail = _read_text(stderr_path).strip()
                    return detail or f"the probe exited with code {code.value}"
                finally:
                    _kernel32().CloseHandle(process.hThread)
                    _kernel32().CloseHandle(process.hProcess)
            finally:
                _kernel32().CloseHandle(job)
                if held_block:
                    _release_network_block()
        finally:
            _advapi32().FreeSid(sid)
    except OSError as exc:
        return str(exc)
    finally:
        if sid is not None:
            try:
                _release_profile_use()
            except OSError:
                pass
        try:
            import shutil

            shutil.rmtree(root, ignore_errors=True)
        except OSError:
            pass


def _probe_env(workspace: Path) -> dict[str, str]:
    env = {}
    for key in (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "SYSTEMDRIVE",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
    ):
        if key in os.environ:
            env[key] = os.environ[key]
    location = str(workspace)
    env["TEMP"] = location
    env["TMP"] = location
    env["HOME"] = location
    env["USERPROFILE"] = location
    env["APPDATA"] = location
    env["LOCALAPPDATA"] = location
    env["PYTHONSAFEPATH"] = "1"
    return env


def ensure_mode(read_paths: Sequence[Path]) -> str:
    """Pick LPAC when it can start Python, otherwise a normal AppContainer."""

    global _mode, _mode_error
    if _mode:
        return _mode
    if _mode_error:
        raise OSError(_mode_error)
    lpac_error = _probe_mode("lpac", read_paths)
    if lpac_error is None:
        _mode = "lpac"
        return _mode
    app_error = _probe_mode("appcontainer", read_paths)
    if app_error is None:
        _mode = "appcontainer"
        return _mode
    _mode_error = (
        "Windows could not start a confined Python. "
        f"The stricter AppContainer failed ({lpac_error}). "
        f"A normal AppContainer failed ({app_error}). "
        "The script was not run."
    )
    raise OSError(_mode_error)


def availability_error(read_paths: Sequence[Path] | None = None) -> str | None:
    """Whether this process can confine a script. The answer is kept for the process."""

    global _probe_done, _probe_result
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return "The Windows sandbox needs 64-bit Python. The script was not run."
    if not read_paths:
        return "The sandbox was not given the Python folders it needs to read."
    if _probe_done:
        return _probe_result
    try:
        ensure_mode(read_paths)
    except OSError as exc:
        _probe_result = str(exc)
    else:
        _probe_result = None
    _probe_done = True
    return _probe_result


def identity_failures(memory_bytes: int) -> list[str]:
    """Checks that run inside the child, before the user's script."""

    _bind()
    failures = []
    kernel32 = _kernel32()
    advapi32 = _advapi32()
    # GetCurrentProcess() is the pseudo-handle -1, and the process token is -4.
    # OpenProcessToken(GetCurrentProcess()) inside this AppContainer raises
    # STATUS_INVALID_HANDLE instead of returning an error. GetTokenInformation
    # accepts the token pseudo-handle, and that handle must not be closed.
    # -4 is the process-token pseudo-handle. -1 is the current process.
    is_container = wintypes.DWORD(0)
    returned = wintypes.DWORD(0)
    try:
        ok = advapi32.GetTokenInformation(
            -4,
            _TokenIsAppContainer,
            ctypes.byref(is_container),
            ctypes.sizeof(is_container),
            ctypes.byref(returned),
        )
    except OSError:
        ok = False
    if not ok:
        failures.append(
            "the AppContainer check did not complete "
            f"(Windows error {_last_error()})"
        )
    elif int(is_container.value) != 1:
        failures.append("the process is not an AppContainer")

    in_job = wintypes.BOOL(False)
    try:
        in_job_ok = bool(kernel32.IsProcessInJob(-1, None, ctypes.byref(in_job)))
    except OSError:
        in_job_ok = False
    if not in_job_ok:
        failures.append(
            "the Job Object check did not complete "
            f"(Windows error {_last_error()})"
        )
        return failures
    if not in_job.value:
        failures.append("the process is not in a Job Object")
        return failures
    info = _JobExtendedLimit()
    length = wintypes.DWORD(0)
    if not kernel32.QueryInformationJobObject(
        None,
        _JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(length),
    ):
        failures.append("the Job Object limits could not be read")
        return failures
    if int(info.ProcessMemoryLimit) <= 0 or int(info.ProcessMemoryLimit) > int(memory_bytes):
        failures.append("the job does not have the memory limit")
    if int(info.BasicLimitInformation.PerProcessUserTimeLimit) <= 0:
        failures.append("the job does not have a CPU time limit")
    return failures


class _TokenAppContainerInformation(ctypes.Structure):
    _fields_ = [("TokenAppContainer", ctypes.c_void_p)]


def _token_group_sids(buffer, read_sid) -> list[str]:
    """Capability SIDs in a 64-bit TOKEN_GROUPS. The caller still holds the buffer."""

    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError("the network capability list needs 64-bit Python")
    raw = bytes(buffer)
    if len(raw) < 8:
        raise OSError("the network capability list was too small")
    count = int.from_bytes(raw[:4], "little")
    if count > 64:
        raise OSError("the network capability list was not readable")
    if len(raw) < 8 + count * 16:
        raise OSError("the network capability list was cut off")
    found: list[str] = []
    for index in range(count):
        start = 8 + index * 16
        pointer = int.from_bytes(raw[start : start + 8], "little")
        if pointer:
            found.append(str(read_sid(pointer)))
    return found


def _token_buffer(advapi32, info_class: int):
    """Bytes of one token class. The buffer stays alive until the caller drops it."""

    returned = wintypes.DWORD(0)
    advapi32.GetTokenInformation(-4, info_class, None, 0, ctypes.byref(returned))
    size = int(returned.value)
    if size <= 0 or size > 1024 * 1024:
        raise OSError(f"the token could not be read (Windows error {_last_error()}).")
    buffer = ctypes.create_string_buffer(size)
    returned = wintypes.DWORD(0)
    if not advapi32.GetTokenInformation(-4, info_class, buffer, size, ctypes.byref(returned)):
        raise OSError(f"the token could not be read (Windows error {_last_error()}).")
    return buffer


def windows_token_network() -> tuple[str, tuple[str, ...] | None]:
    """This process's package SID and capability SIDs.

    The capability tuple is None when the list could not be read. An empty
    tuple means the token has none. The process-token pseudo-handle is not closed.
    """

    _bind()
    advapi32 = _advapi32()
    package = "unread"
    try:
        raw = _token_buffer(advapi32, _TokenAppContainerSid)
        try:
            info = _TokenAppContainerInformation.from_buffer(raw)
            if not info.TokenAppContainer:
                package = "unread empty"
            else:
                package = _sid_to_string(ctypes.c_void_p(info.TokenAppContainer))
        finally:
            del raw
    except (OSError, ValueError) as exc:
        package = f"unread {exc}"
    try:
        raw = _token_buffer(advapi32, _TokenCapabilities)
        try:
            capabilities = tuple(
                _token_group_sids(
                    raw,
                    lambda pointer: _sid_to_string(ctypes.c_void_p(pointer)),
                )
            )
        finally:
            del raw
    except (OSError, ValueError) as exc:
        return f"{package}; capabilities unread ({exc})", None
    return package, capabilities


def network_error_is_confined(error: OSError) -> bool:
    winerror = getattr(error, "winerror", None)
    # 5 and 10013 are access denied. 10051 is network unreachable, which is what a
    # blocked stack reports. A timeout (10060), host unreachable (10065), or a DNS
    # failure means the socket was not refused.
    return winerror in {5, 10013, 10051} or error.errno in {13, 1}


def system_file_probe() -> str:
    windir = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
    return str(Path(windir) / "win.ini")


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


_directory_watch_bound = False


def _bind_directory_watch(kernel32) -> None:
    global _directory_watch_bound
    if _directory_watch_bound:
        return
    kernel32.ReadDirectoryChangesW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.ReadDirectoryChangesW.restype = wintypes.BOOL
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    kernel32.ResetEvent.restype = wintypes.BOOL
    kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    kernel32.GetOverlappedResult.restype = wintypes.BOOL
    kernel32.CancelIo.argtypes = [wintypes.HANDLE]
    kernel32.CancelIo.restype = wintypes.BOOL
    kernel32.GetLastError.restype = wintypes.DWORD
    _directory_watch_bound = True


def _apply_directory_events(buffer, nbytes: int, root: Path, retain, release) -> None:
    """Report each file name in a ``ReadDirectoryChangesW`` buffer."""
    offset = 0
    data = bytes(buffer)[:nbytes]
    while offset + 12 <= len(data):
        next_offset = int.from_bytes(data[offset : offset + 4], "little")
        action = int.from_bytes(data[offset + 4 : offset + 8], "little")
        name_len = int.from_bytes(data[offset + 8 : offset + 12], "little")
        if name_len < 0 or offset + 12 + name_len > len(data):
            break
        name = data[offset + 12 : offset + 12 + name_len].decode("utf-16-le", errors="replace")
        path = Path(root) / name
        # 1 added, 3 modified, 5 renamed to this name. 2 removed, 4 renamed away.
        if action in (1, 3, 5):
            retain(path)
        elif action in (2, 4):
            release(path)
        if next_offset <= 0:
            break
        offset += next_offset


class _FindData(ctypes.Structure):
    """WIN32_FIND_DATAW. The name fields have to be present so the call does not write past the struct."""

    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("dwReserved0", wintypes.DWORD),
        ("dwReserved1", wintypes.DWORD),
        ("cFileName", wintypes.WCHAR * 260),
        ("cAlternateFileName", wintypes.WCHAR * 14),
    ]


_find_bound = False


def shared_read(path: Path) -> int | None:
    """A read descriptor that shares delete.

    The script can replace the file while this descriptor stays open, and the
    size can still be read after the name is gone. ``os.open`` does not share
    delete, so it is not used here.
    """
    if sys.platform != "win32":
        return None
    _bind()
    kernel32 = _kernel32()
    handle = kernel32.CreateFileW(
        str(path),
        0x1 | 0x80 | 0x100000,  # read data, read attributes, synchronize
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        0x80,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    if not handle or handle == _INVALID_HANDLE:
        return None
    import msvcrt

    try:
        return msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except (OSError, ValueError):
        kernel32.CloseHandle(handle)
        return None


def named_size(path: Path) -> int | None:
    """The directory entry's size. This does not leave the file open.

    An open read handle would stop the script from replacing the file. The
    directory entry still reports the size, including while another open
    holds the file.
    """
    if sys.platform != "win32":
        return None
    _bind()
    kernel32 = _kernel32()
    global _find_bound
    if not _find_bound:
        kernel32.FindFirstFileW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_FindData)]
        kernel32.FindFirstFileW.restype = wintypes.HANDLE
        kernel32.FindClose.argtypes = [wintypes.HANDLE]
        kernel32.FindClose.restype = wintypes.BOOL
        _find_bound = True
    data = _FindData()
    handle = kernel32.FindFirstFileW(str(path), ctypes.byref(data))
    if not handle or handle == _INVALID_HANDLE:
        return None
    kernel32.FindClose(handle)
    if int(data.dwFileAttributes) & 0x10:
        return None
    if int(data.dwFileAttributes) & 0x400:
        return None
    return (int(data.nFileSizeHigh) << 32) | int(data.nFileSizeLow)


def cancel_directory_watch(handle) -> None:
    """Wake a directory read started on another thread.

    ``CancelIo`` only cancels reads started by the calling thread. The watch
    thread owns the read, so stopping it uses ``CancelIoEx``.
    """

    if sys.platform != "win32" or not handle:
        return
    kernel32 = _kernel32()
    cancel = getattr(kernel32, "CancelIoEx", None)
    if cancel is None:
        return
    cancel.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    cancel.restype = wintypes.BOOL
    cancel(handle, None)


def run_directory_watch(root, retain, release, reread, scan, stop, mark, remember=None) -> None:
    """Watch ``root`` until ``stop`` is set.

    ``ReadDirectoryChangesW`` reports a new file when its name appears, which
    is sooner than the next timer. The timer used to wait 100 ms, so a file
    created and removed in that gap was never counted.
    """
    if sys.platform != "win32":
        mark(False)
        return
    _bind()
    kernel32 = _kernel32()
    _bind_directory_watch(kernel32)
    handle = kernel32.CreateFileW(
        str(root),
        0x0001,  # FILE_LIST_DIRECTORY
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | 0x00000004,
        None,
        _OPEN_EXISTING,
        0x02000000 | 0x40000000,  # FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED
        None,
    )
    if not handle or handle == _INVALID_HANDLE:
        mark(False)
        return
    if remember is not None:
        remember(handle)
    event = kernel32.CreateEventW(None, True, False, None)
    if not event:
        if remember is not None:
            remember(None)
        kernel32.CloseHandle(handle)
        mark(False)
        return
    overlapped = _Overlapped()
    overlapped.hEvent = event
    buffer = ctypes.create_string_buffer(64 * 1024)
    # File and directory names, size, and last write.
    notify = 0x00000001 | 0x00000002 | 0x00000008 | 0x00000010
    pending = False

    def issue() -> bool:
        nonlocal pending
        kernel32.ResetEvent(event)
        ok = kernel32.ReadDirectoryChangesW(
            handle,
            buffer,
            ctypes.sizeof(buffer),
            True,
            notify,
            None,
            ctypes.byref(overlapped),
            None,
        )
        if not ok and int(kernel32.GetLastError()) != 997:
            return False
        pending = True
        return True

    try:
        if not issue():
            mark(False)
            return
        def note_tree() -> None:
            for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
                if stop.is_set():
                    return
                for name in filenames:
                    retain(Path(dirpath) / name)

        mark(True)
        while not stop.is_set():
            try:
                note_tree()
            except OSError:
                pass
            reread()
            waited = int(kernel32.WaitForSingleObject(event, 1))
            if stop.is_set():
                break
            if waited == _WAIT_OBJECT_0 and pending:
                transferred = wintypes.DWORD(0)
                got = kernel32.GetOverlappedResult(
                    handle, ctypes.byref(overlapped), ctypes.byref(transferred), False
                )
                pending = False
                if got and transferred.value:
                    _apply_directory_events(buffer, int(transferred.value), Path(root), retain, release)
                elif not got and int(kernel32.GetLastError()) == 1022:
                    scan()
                if not issue():
                    break
            reread()
            scan()
    finally:
        if remember is not None:
            remember(None)
        try:
            kernel32.CancelIo(handle)
        except OSError:
            pass
        kernel32.CloseHandle(event)
        kernel32.CloseHandle(handle)
