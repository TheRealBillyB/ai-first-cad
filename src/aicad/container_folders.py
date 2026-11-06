"""Private folders inside the Windows script container.

Python 3.11.10 and later, 3.12.4 and later, and 3.13 and later create
``os.mkdir(path, 0o700)`` on Windows with an explicit owner-only access list.
An AppContainer may not create a folder with an explicit access list, so that
call fails with "Access denied". ``tempfile.mkdtemp`` retries that error for as
long as ``os.access`` calls the folder writable, which on Windows reads only the
read-only flag, and ``TMP_MAX`` is about two billion there. aicad makes such a
folder while it loads, so every script and solve spun until the job's CPU limit.

Inside a container only, a private folder is made the plain way. It then
inherits the folder it is in, and that list is checked: it may name only
SYSTEM, Administrators, the owner, this account, and this container, which are
the accounts Python's own list names plus the container. A broader list is
removed and refused. ``TMP_MAX`` is lowered, so a refusal ends at once. Outside
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
            return [(_ACCESS_ALLOWED_ACE_TYPE, "S-1-1-0")]

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
            kind = ctypes.string_at(ace.value, 1)[0]
            # ACCESS_ALLOWED_ACE and ACCESS_DENIED_ACE: a 4-byte header, a 4-byte mask, then the SID.
            sid = _sid_text(advapi, ace.value + 8) if kind in (_ACCESS_ALLOWED_ACE_TYPE, _ACCESS_DENIED_ACE_TYPE) else None
            aces.append((kind, sid or f"(entry of kind {kind})"))
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
    real_mkdir = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        try:
            return real_mkdir(path, mode, dir_fd=dir_fd)
        except PermissionError as exc:
            if mode != _PRIVATE_MODE or dir_fd is not None or getattr(exc, "winerror", None) != _ACCESS_DENIED:
                raise
        real_mkdir(path, dir_fd=dir_fd)
        try:
            broader = unexpected_grants(folder_aces(os.fspath(path)), allowed)
        except OSError:
            broader = ["(the access list could not be read)"]
        if broader:
            try:
                os.rmdir(path)
            except OSError:
                pass
            # Not a PermissionError, so tempfile stops instead of trying another name.
            raise OSError(
                errno.ENOTSUP,
                "A private folder here would also be open to " + ", ".join(broader)
                + ". Keep the project in a folder only your account can open, such as Documents.",
                os.fspath(path),
            )
        return None

    mkdir._aicad_container = True
    os.mkdir = mkdir
    import tempfile

    tempfile.TMP_MAX = min(tempfile.TMP_MAX, _CONTAINER_TMP_MAX)
