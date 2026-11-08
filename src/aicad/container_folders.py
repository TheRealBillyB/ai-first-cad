"""Private folders inside the Windows script container.

Python 3.11.10 and later, 3.12.4 and later, and 3.13 and later create
``os.mkdir(path, 0o700)`` on Windows with an explicit owner-only access list.
An AppContainer may not be able to create a folder with an explicit access
list, so that call can fail with "Access denied". On other folders it
succeeds, and the list it writes does not name the container, so the
container that made the folder cannot open it. Opening the job then failed
with "Access denied" while aicad loaded. ``tempfile.mkdtemp`` retries that error for as
long as ``os.access`` calls the folder writable, which on Windows reads only the
read-only flag, and ``TMP_MAX`` is about two billion there. aicad makes such a
folder while it loads, so every script and solve spun until the job's CPU limit.

Inside a container only, a private folder is always made the plain way, without
trying the owner-only list first. It then
inherits the folder it is in, and that list is checked: it may name only
SYSTEM, Administrators, the owner, this account, and this container, which are
the accounts Python's own list names plus the container. An app container
(S-1-15-2-...) that reaches the folder only by inheritance is also accepted:
Claude Desktop, for example, gives its own container the whole user profile,
so every project inherits it. Each such container is named once on stderr,
and the caller adds its package name. The offline script never accepts aicad's network
container. Any other account, such as Users or Everyone, makes the list
broader, and it is removed and refused. ``TMP_MAX`` is lowered, so a refusal ends at once. Outside
a container nothing changes. Only the standard library is used, because this
runs before the rest of aicad loads.
"""

from __future__ import annotations

import errno
import os
import sys

_PRIVATE_MODE = 0o700
_ACCESS_DENIED = 5
# Tries before tempfile gives up. Random names do not collide 100 times.
_CONTAINER_TMP_MAX = 100
_TOKEN_USER = 1
_TOKEN_IS_APP_CONTAINER = 29
_TOKEN_APP_CONTAINER_SID = 31
_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 4
_ACL_SIZE_INFORMATION = 2
_ACCESS_ALLOWED_ACE_TYPE = 0
_INHERITED_ACE = 0x10
_APP_CONTAINER_PREFIX = "S-1-15-2-"
# The container names in sandbox_setup_windows and sandbox_windows. This file
# loads before aicad, so they are repeated here; a test keeps them equal.
_OFFLINE_CONTAINER_NAME = "aicadscript"
CONTAINER_NOTE = "aicad: these app containers can also open this project's private folders:"
_NETWORK_CONTAINER_NAME = "aicadscriptnet"
_ACCESS_DENIED_ACE_TYPE = 1
# The accounts Python's own owner-only list names: SYSTEM, Administrators, and
# OWNER RIGHTS. CREATOR OWNER only passes the owner on to new children.
_PYTHON_PRIVATE_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-4", "S-1-3-0"})


def unexpected_grants(aces: list[tuple[int, str]], allowed: frozenset[str]) -> list[str]:
    """Accounts an access list lets in beyond ``allowed``. A deny entry takes nothing away from privacy."""
    found = []
    for kind, sid in aces:
        if kind == _ACCESS_DENIED_ACE_TYPE:
            continue
        # Any other allow form, such as an object or callback entry, is treated as broader.
        if kind != _ACCESS_ALLOWED_ACE_TYPE or sid not in allowed:
            found.append(sid)
    return found


def private_folder_check(
    entries: list[tuple[int, str, bool]], allowed: frozenset[str], refused_containers: frozenset[str] = frozenset()
) -> tuple[list[str], list[str], list[str]]:
    """Sort a new private folder's entries: (broader accounts, refused containers, outside app containers).

    A deny entry takes nothing away from privacy. An allow entry for an app
    container that the folder only inherits is accepted and reported. One set
    directly on the folder, or for a refused container, is not.
    """
    broader: list[str] = []
    refused: list[str] = []
    outside: list[str] = []
    for kind, sid, inherited in entries:
        if kind == _ACCESS_DENIED_ACE_TYPE:
            continue
        if sid in refused_containers:
            if sid not in refused:
                refused.append(sid)
        elif kind == _ACCESS_ALLOWED_ACE_TYPE and sid in allowed:
            continue
        elif kind == _ACCESS_ALLOWED_ACE_TYPE and inherited and sid.startswith(_APP_CONTAINER_PREFIX):
            if sid not in outside:
                outside.append(sid)
        else:
            broader.append(sid)
    return broader, refused, outside


def container_sid(name: str) -> str | None:
    """The SID Windows derives for an app container name."""
    import ctypes

    userenv = ctypes.WinDLL("userenv")
    pointer = ctypes.c_void_p()
    if userenv.DeriveAppContainerSidFromAppContainerName(ctypes.c_wchar_p(name), ctypes.byref(pointer)) != 0:
        return None
    try:
        return _sid_text(_advapi32(), pointer.value)
    finally:
        ctypes.WinDLL("advapi32").FreeSid(pointer)


_MAPPINGS = "Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\CurrentVersion\\AppContainer\\Mappings\\"


def package_name(sid: str) -> str:
    """The package moniker Windows records for an app container, or "unknown package"."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MAPPINGS + sid) as found:
            return str(winreg.QueryValueEx(found, "Moniker")[0])
    except OSError:
        return "unknown package"


def _advapi32():
    import ctypes

    return ctypes.WinDLL("advapi32", use_last_error=True)


def _token_sid(advapi, klass: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    buffer = ctypes.create_string_buffer(256)
    needed = wintypes.DWORD()
    # GetCurrentProcessToken() is the pseudo handle -4.
    if not advapi.GetTokenInformation(wintypes.HANDLE(-4), klass, buffer, len(buffer), ctypes.byref(needed)):
        return None
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    if not pointer:
        return None
    return _sid_text(advapi, pointer)


def _sid_text(advapi, pointer) -> str | None:
    import ctypes
    from ctypes import wintypes

    text = wintypes.LPWSTR()
    if not advapi.ConvertSidToStringSidW(ctypes.c_void_p(pointer), ctypes.byref(text)):
        return None
    try:
        return text.value
    finally:
        ctypes.windll.kernel32.LocalFree(text)


def _in_app_container(advapi) -> bool:
    import ctypes
    from ctypes import wintypes

    value = wintypes.DWORD()
    needed = wintypes.DWORD()
    found = advapi.GetTokenInformation(
        wintypes.HANDLE(-4), _TOKEN_IS_APP_CONTAINER, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(needed)
    )
    return bool(found and value.value)


def folder_aces(path: str) -> list[tuple[int, str]]:
    """The entries of a folder's access list, as (kind, SID text)."""
    return [(kind, sid) for kind, sid, _inherited in folder_entries(path)]


def folder_entries(path: str) -> list[tuple[int, str, bool]]:
    """The entries of a folder's access list, as (kind, SID text, inherited)."""
    import ctypes
    from ctypes import wintypes

    advapi = _advapi32()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    code = advapi.GetNamedSecurityInfoW(
        path, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor)
    )
    if code:
        raise OSError(errno.EACCES, f"The new folder's access list could not be read (error {code}).", path)
    try:
        if not dacl.value:
            # No list at all lets everyone in.
            return [(_ACCESS_ALLOWED_ACE_TYPE, "S-1-1-0", False)]

        class _SizeInformation(ctypes.Structure):
            _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD), ("AclBytesFree", wintypes.DWORD)]

        size = _SizeInformation()
        if not advapi.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), _ACL_SIZE_INFORMATION):
            raise OSError(errno.EACCES, "The new folder's access list could not be read.", path)
        aces = []
        for index in range(size.AceCount):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise OSError(errno.EACCES, "The new folder's access list could not be read.", path)
            kind, flags = ctypes.string_at(ace.value, 2)
            # ACCESS_ALLOWED_ACE and ACCESS_DENIED_ACE: a 4-byte header, a 4-byte mask, then the SID.
            sid = _sid_text(advapi, ace.value + 8) if kind in (_ACCESS_ALLOWED_ACE_TYPE, _ACCESS_DENIED_ACE_TYPE) else None
            aces.append((kind, sid or f"(entry of kind {kind})", bool(flags & _INHERITED_ACE)))
        return aces
    finally:
        ctypes.windll.kernel32.LocalFree(descriptor)


def make_private_folders_work_in_a_container() -> None:
    """Inside a Windows AppContainer, let ``os.mkdir(path, 0o700)`` make a folder only private accounts can open."""
    if sys.platform != "win32" or getattr(os.mkdir, "_aicad_container", False):
        return
    advapi = _advapi32()
    if not _in_app_container(advapi):
        return
    container = _token_sid(advapi, _TOKEN_APP_CONTAINER_SID)
    user = _token_sid(advapi, _TOKEN_USER)
    allowed = _PYTHON_PRIVATE_SIDS | {sid for sid in (container, user) if sid}
    network = container_sid(_NETWORK_CONTAINER_NAME)
    # Only the offline container refuses the network one. Its own SID is in ``allowed``.
    refused_containers = frozenset({network}) if network and network != container else frozenset()
    reported: set[str] = set()
    real_mkdir = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        if mode != _PRIVATE_MODE or dir_fd is not None:
            return real_mkdir(path, mode, dir_fd=dir_fd)
        # Python's owner-only list never names this container. Depending on the
        # folder it is in, that list is refused or it locks this container out
        # of its own folder. The inherited list, checked below, works either way.
        real_mkdir(path)
        try:
            broader, refused, outside = private_folder_check(folder_entries(os.fspath(path)), allowed, refused_containers)
        except OSError:
            broader, refused, outside = ["(the access list could not be read)"], [], []
        if refused or broader:
            try:
                os.rmdir(path)
            except OSError:
                pass
            if refused:
                message = (
                    "A private folder here would also be open to aicad's network script container. "
                    "An offline script never shares its folders with it. Keep the project in a folder "
                    "that does not grant that container access."
                )
            else:
                message = (
                    "A private folder here would also be open to " + ", ".join(broader)
                    + ". Keep the project in a folder only your account can open, such as Documents."
                )
            # Not a PermissionError, so tempfile stops instead of trying another name.
            raise OSError(errno.ENOTSUP, message, os.fspath(path))
        new = [sid for sid in outside if sid not in reported]
        if new:
            reported.update(new)
            # The container cannot read the package names. The caller adds them.
            print(f"{CONTAINER_NOTE} {', '.join(new)}", file=sys.stderr)
        return None

    mkdir._aicad_container = True
    os.mkdir = mkdir
    import tempfile

    tempfile.TMP_MAX = min(tempfile.TMP_MAX, _CONTAINER_TMP_MAX)
