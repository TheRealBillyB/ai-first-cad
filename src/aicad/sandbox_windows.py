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
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import subprocess
import sys
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
_STARTF_USESTDHANDLES = 0x00000100

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
_JobObjectExtendedLimitInformation = 9

_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x00000102
_TOKEN_QUERY = 0x0008
_TokenIsAppContainer = 29
_ERROR_ALREADY_EXISTS = 0x800700B7
_INVALID_HANDLE = ctypes.c_void_p(-1).value

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_CREATE_ALWAYS = 2
_FILE_ATTRIBUTE_NORMAL = 0x80

_mode: str | None = None
_mode_error: str | None = None
_sid_string: str | None = None

# One directory finishes immediately. A tree walk of the Python install can
# take longer. Either one that does not finish is waiting, not working.
_ICACLS_TIMEOUT_SECONDS = 15
_ICACLS_TREE_TIMEOUT_SECONDS = 60

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_GRANT_ACCESS = 1
_NO_MULTIPLE_TRUSTEE = 0
_TRUSTEE_IS_SID = 0
_TRUSTEE_IS_UNKNOWN = 0
_NO_INHERITANCE = 0
# Traverse and read attributes. This does not include listing the directory,
# so a parent grant does not reveal the names of its other children.
_FILE_GENERIC_EXECUTE = 0x1200A0


class IcaclsTimeout(OSError):
    """icacls did not finish, so the folder was not granted."""


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


class _Trustee(ctypes.Structure):
    _fields_ = [
        ("pMultipleTrustee", ctypes.c_void_p),
        ("MultipleTrusteeOperation", wintypes.DWORD),
        ("TrusteeForm", wintypes.DWORD),
        ("TrusteeType", wintypes.DWORD),
        ("ptstrName", ctypes.c_void_p),
    ]


class _ExplicitAccess(ctypes.Structure):
    _fields_ = [
        ("grfAccessPermissions", wintypes.DWORD),
        ("grfAccessMode", wintypes.DWORD),
        ("grfInheritance", wintypes.DWORD),
        ("Trustee", _Trustee),
    ]


def _kernel32() -> ctypes.WinDLL:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _userenv() -> ctypes.WinDLL:
    return ctypes.WinDLL("userenv", use_last_error=True)


def _advapi32() -> ctypes.WinDLL:
    return ctypes.WinDLL("advapi32", use_last_error=True)


def _win_error(prefix: str) -> str:
    code = ctypes.get_last_error()
    return f"{prefix} (Windows error {code})."


def _bind() -> None:
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
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
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


def _sid_to_string(sid: ctypes.c_void_p) -> str:
    out = ctypes.c_void_p()
    if not _advapi32().ConvertSidToStringSidW(sid, ctypes.byref(out)):
        raise OSError(_win_error("Could not read the AppContainer id"))
    try:
        return ctypes.cast(out, wintypes.LPWSTR).value or ""
    finally:
        _kernel32().LocalFree(out)


def _appcontainer_sid() -> tuple[ctypes.c_void_p, str]:
    userenv = _userenv()
    sid = ctypes.c_void_p()
    result = userenv.CreateAppContainerProfile(
        _PROFILE_NAME,
        _PROFILE_DISPLAY,
        _PROFILE_DESCRIPTION,
        None,
        0,
        ctypes.byref(sid),
    )
    if result != 0:
        if (result & 0xFFFFFFFF) != _ERROR_ALREADY_EXISTS:
            raise OSError(
                f"Windows could not create an AppContainer profile (code 0x{result & 0xFFFFFFFF:08X})."
            )
        derived = userenv.DeriveAppContainerSidFromAppContainerName(_PROFILE_NAME, ctypes.byref(sid))
        if derived != 0:
            raise OSError(
                f"Windows could not open the AppContainer profile (code 0x{derived & 0xFFFFFFFF:08X})."
            )
    text = _sid_to_string(sid)
    return sid, text


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


def _bind_acl() -> None:
    _bind()
    advapi32 = _advapi32()
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.SetEntriesInAclW.argtypes = [
        wintypes.ULONG,
        ctypes.POINTER(_ExplicitAccess),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.SetEntriesInAclW.restype = wintypes.DWORD
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD


def _grant_traverse_one(path: Path, sid: str) -> None:
    """Add traverse on this directory only. Children are not listed or changed.

    icacls on a parent such as the tool cache does not return: it asks for
    confirmation or walks more than that folder. The access call writes one
    entry and does not scan the children.
    """

    _bind_acl()
    advapi32 = _advapi32()
    kernel32 = _kernel32()
    sid_ptr = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(sid, ctypes.byref(sid_ptr)):
        raise OSError(_win_error(f"Could not read the sandbox id for {path}"))
    descriptor = ctypes.c_void_p()
    new_dacl = ctypes.c_void_p()
    try:
        trustee = _Trustee()
        trustee.pMultipleTrustee = None
        trustee.MultipleTrusteeOperation = _NO_MULTIPLE_TRUSTEE
        trustee.TrusteeForm = _TRUSTEE_IS_SID
        trustee.TrusteeType = _TRUSTEE_IS_UNKNOWN
        trustee.ptstrName = sid_ptr
        explicit = _ExplicitAccess()
        explicit.grfAccessPermissions = _FILE_GENERIC_EXECUTE
        explicit.grfAccessMode = _GRANT_ACCESS
        explicit.grfInheritance = _NO_INHERITANCE
        explicit.Trustee = trustee

        dacl = ctypes.c_void_p()
        status = advapi32.GetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if status != 0:
            raise OSError(f"Could not read access for {path} (code {status}).")
        # A missing DACL already allows access. Replacing it would lock the folder.
        if not dacl:
            return
        status = advapi32.SetEntriesInAclW(1, ctypes.byref(explicit), dacl, ctypes.byref(new_dacl))
        if status != 0:
            raise OSError(f"Could not add traverse for {path} (code {status}).")
        mutable = ctypes.create_unicode_buffer(str(path))
        status = advapi32.SetNamedSecurityInfoW(
            mutable,
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            new_dacl,
            None,
        )
        if status != 0:
            raise OSError(f"Could not grant traverse on {path} (code {status}).")
    finally:
        if new_dacl:
            kernel32.LocalFree(new_dacl)
        if descriptor:
            kernel32.LocalFree(descriptor)
        if sid_ptr:
            kernel32.LocalFree(sid_ptr)


def _grant_traverse(path: Path, sid: str) -> None:
    """Let the container walk down to a folder without granting that folder's children."""

    # The leaf is granted separately. Parents only need traverse. The volume
    # root is not granted: that would be an access entry on the whole drive.
    if os.name == "nt":
        try:
            text = str(path.resolve())
        except OSError:
            text = str(path)
    else:
        text = str(path)
    for parent in _windows_traverse_parents(text):
        try:
            _grant_traverse_one(Path(parent), sid)
        except IcaclsTimeout:
            raise
        except OSError:
            # Some system folders are not ours to edit. The container can
            # still walk them when Windows already allows it.
            continue


def _marker_path() -> Path:
    return Path(sys.prefix) / ".aicad-appcontainer-acl"


def grant_read_paths(paths: Sequence[Path], sid: str) -> None:
    marker = _marker_path()
    already = False
    try:
        already = marker.is_file() and marker.read_text(encoding="utf-8").strip() == sid
    except OSError:
        already = False
    if not already:
        for path in paths:
            _grant_tree(path, sid, "(OI)(CI)RX")
            _grant_traverse(path, sid)
        try:
            marker.write_text(sid, encoding="utf-8")
        except OSError:
            pass


def grant_workspace(path: Path, sid: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _grant_tree(path, sid, "(OI)(CI)M")
    _grant_traverse(path, sid)


_user_sid: str | None = None
_user_sid_checked = False


def _current_user_sid() -> str | None:
    """SID of the person running aicad, so files the container creates stay readable."""

    global _user_sid, _user_sid_checked
    if _user_sid_checked:
        return _user_sid
    _user_sid_checked = True
    completed = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        check=False,
    )
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
) -> _ProcessInformation:
    kernel32 = _kernel32()
    advapi32 = _advapi32()
    stdin = _open_inherited("NUL", _GENERIC_READ, _OPEN_EXISTING)
    stdout = _open_inherited(str(stdout_path), _GENERIC_WRITE, _CREATE_ALWAYS)
    stderr = _open_inherited(str(stderr_path), _GENERIC_WRITE, _CREATE_ALWAYS)
    capability_sid = ctypes.c_void_p()
    capabilities = None
    try:
        security = _SecurityCapabilities()
        security.AppContainerSid = sid
        security.CapabilityCount = 0
        security.Capabilities = None
        if network:
            # internetClient. Without this capability the container has no network.
            if not advapi32.ConvertStringSidToSidW("S-1-15-3-1", ctypes.byref(capability_sid)):
                raise OSError(_win_error("Could not look up the network capability"))
            capabilities = _SidAndAttributes()
            capabilities.Sid = capability_sid
            capabilities.Attributes = 0x4  # SE_GROUP_ENABLED
            security.Capabilities = ctypes.cast(ctypes.pointer(capabilities), ctypes.c_void_p)
            security.CapabilityCount = 1

        attribute_count = 4 if lpac else 3
        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, attribute_count, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(
            attributes, attribute_count, 0, ctypes.byref(size)
        ):
            raise OSError(_win_error("Could not prepare the process attributes"))
        try:
            if not kernel32.UpdateProcThreadAttribute(
                attributes,
                0,
                _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(security),
                ctypes.sizeof(security),
                None,
                None,
            ):
                raise OSError(_win_error("Could not attach the AppContainer"))
            job_list = (wintypes.HANDLE * 1)(job)
            if not kernel32.UpdateProcThreadAttribute(
                attributes,
                0,
                _PROC_THREAD_ATTRIBUTE_JOB_LIST,
                job_list,
                ctypes.sizeof(job_list),
                None,
                None,
            ):
                raise OSError(_win_error("Could not put the script in the Job Object"))
            handles = (wintypes.HANDLE * 3)(stdin, stdout, stderr)
            if not kernel32.UpdateProcThreadAttribute(
                attributes,
                0,
                _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                handles,
                ctypes.sizeof(handles),
                None,
                None,
            ):
                raise OSError(_win_error("Could not pass the script's output handles"))
            policy = wintypes.DWORD(_PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT)
            if lpac and not kernel32.UpdateProcThreadAttribute(
                attributes,
                0,
                _PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY,
                ctypes.byref(policy),
                ctypes.sizeof(policy),
                None,
                None,
            ):
                raise OSError(_win_error("Could not request the stricter AppContainer"))

            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = stdin
            startup.StartupInfo.hStdOutput = stdout
            startup.StartupInfo.hStdError = stderr
            startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)

            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            environment = _environment_block(env)
            process = _ProcessInformation()
            created = kernel32.CreateProcessW(
                command[0],
                command_line,
                None,
                None,
                True,
                _CREATE_SUSPENDED
                | _CREATE_UNICODE_ENVIRONMENT
                | _CREATE_NO_WINDOW
                | _EXTENDED_STARTUPINFO_PRESENT,
                ctypes.cast(environment, ctypes.c_void_p),
                cwd,
                ctypes.byref(startup),
                ctypes.byref(process),
            )
            if not created:
                raise OSError(_win_error("Could not start the sandboxed script"))
            if kernel32.ResumeThread(process.hThread) == 0xFFFFFFFF:
                kernel32.TerminateJobObject(job, 1)
                kernel32.CloseHandle(process.hThread)
                kernel32.CloseHandle(process.hProcess)
                raise OSError(_win_error("Could not resume the sandboxed script"))
            return process
        finally:
            kernel32.DeleteProcThreadAttributeList(attributes)
    finally:
        kernel32.CloseHandle(stdin)
        kernel32.CloseHandle(stdout)
        kernel32.CloseHandle(stderr)
        if capability_sid:
            kernel32.LocalFree(capability_sid)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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


# JobObjectLimitViolationInformation. The first two fields are enough to see
# whether the job stopped the process for CPU time or for memory.
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
) -> tuple[int, str, str, bool, bool, str]:
    """Start one confined process. Returns code, stdout, stderr, timed_out, memory_killed, mechanism."""

    _bind()
    mode = ensure_mode(read_paths)
    sid, sid_text = _appcontainer_sid()
    job = None
    process = None
    try:
        # Do not rewrite this process's access list. An AppContainer can open
        # a process only when that process grants the container, and this
        # process does not. Adding a deny entry here stopped later scripts
        # from finishing in CI. See SECURITY.md.
        grant_workspace(workspace, sid_text)
        job = _create_job(cpu_seconds, memory_bytes)
        process = _launch(
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
        wait_ms = max(1, int(timeout_seconds * 1000))
        waited = _kernel32().WaitForSingleObject(process.hProcess, wait_ms)
        timed_out = waited == _WAIT_TIMEOUT
        if timed_out:
            _kernel32().TerminateJobObject(job, 1)
            _kernel32().WaitForSingleObject(process.hProcess, 5000)
        code = wintypes.DWORD(0)
        _kernel32().GetExitCodeProcess(process.hProcess, ctypes.byref(code))
        # The stored code is unsigned. A kill shows up above the normal exit range.
        exit_code = int(code.value)
        if exit_code >= 0x80000000:
            exit_code = exit_code - 0x100000000
        # A CPU-limit kill often leaves a normal-looking exit code. The job's
        # own violation flags are what say the limit stopped the script.
        memory_killed = (
            not timed_out
            and exit_code != 0
            and (_job_limit_violated(job) or _job_memory_killed(job, memory_bytes))
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
        # Terminate the job on the way out, not only on the wall-clock timeout,
        # so a child the script started does not keep running after the tool returns.
        if job is not None:
            _kernel32().TerminateJobObject(job, 1)
        if process is not None:
            _kernel32().CloseHandle(process.hThread)
            _kernel32().CloseHandle(process.hProcess)
        if job is not None:
            _kernel32().CloseHandle(job)
        _advapi32().FreeSid(sid)


def _probe_command(workspace: Path) -> list[str]:
    script = workspace / "probe.py"
    # The real script imports aicad. A probe that only prints can succeed in a
    # container that then fails to load the geometry libraries.
    script.write_text("import aicad\nprint('sandbox-ok')\n", encoding="utf-8")
    return [sys.executable, "-B", "-u", str(script)]


def _probe_mode(mode: str, read_paths: Sequence[Path]) -> str | None:
    """Return an error string when this AppContainer mode cannot start Python."""

    import tempfile

    _bind()
    root = Path(tempfile.mkdtemp(prefix="aicad-win-probe-"))
    try:
        sid, sid_text = _appcontainer_sid()
        try:
            grant_read_paths(read_paths, sid_text)
            grant_workspace(root, sid_text)
            job = _create_job(30, 2 * 1024 * 1024 * 1024)
            try:
                stdout_path = root / "stdout.txt"
                stderr_path = root / "stderr.txt"
                process = _launch(
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
        finally:
            _advapi32().FreeSid(sid)
    except OSError as exc:
        return str(exc)
    finally:
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
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return "The Windows sandbox needs 64-bit Python. The script was not run."
    if not read_paths:
        return "The sandbox was not given the Python folders it needs to read."
    try:
        ensure_mode(read_paths)
    except OSError as exc:
        return str(exc)
    return None


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
    if not ok or is_container.value != 1:
        failures.append("the process is not an AppContainer")

    in_job = wintypes.BOOL(False)
    try:
        in_job_ok = bool(kernel32.IsProcessInJob(-1, None, ctypes.byref(in_job)))
    except OSError:
        in_job_ok = False
    if not in_job_ok or not in_job.value:
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


def network_error_is_confined(error: OSError) -> bool:
    winerror = getattr(error, "winerror", None)
    # 5 and 10013 are access denied. 10051 is network unreachable, which is what a
    # blocked stack reports. A timeout or a reset means the packet left the machine.
    return winerror in {5, 10013, 10051} or error.errno in {13, 1}


def system_file_probe() -> str:
    windir = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
    return str(Path(windir) / "win.ini")
