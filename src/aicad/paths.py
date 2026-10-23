"""Project path checks that mean the same thing on Windows and POSIX.

A session file can be written on one machine and opened on another. Drive
letters, UNC shares, rooted paths, and ``..`` are decided from the text, with
both Windows and POSIX rules, before this process joins the path onto the
project or touches the file.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath


def session_path_escapes(text: str) -> bool:
    """True when a session path is not a relative file inside a project.

    Absolute paths, drive letters (``C:`` and ``C:\\``), UNC and ``\\\\?\\``
    paths, a leading slash or backslash, and ``..`` with either separator are
    escapes. A backslash is a separator on Windows even when this process is
    not.
    """
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if posix.is_absolute() or ".." in posix.parts:
        return True
    if windows.is_absolute() or windows.drive or windows.root or ".." in windows.parts:
        return True
    return False


def require_session_file(root: Path, raw: object, label: str, error: type[Exception]) -> Path:
    """A session path that stays inside ``root``. The file is not opened here."""
    if not isinstance(raw, str) or not raw.strip():
        raise error(f"{label} is missing.")
    if "\x00" in raw or session_path_escapes(raw.strip()):
        raise error(f"{label} {raw!r} is outside the project. It was not read.")
    root_resolved = Path(root).resolve()
    full = (root_resolved / raw.strip()).resolve()
    if not _is_inside(full, root_resolved):
        raise error(f"{label} {raw!r} is outside the project. It was not read.")
    return full


def require_export_path(root: Path, raw: str | Path, label: str, error: type[Exception]) -> Path:
    """An assistant export path that stays inside ``root``.

    A relative path is joined to the project. An absolute path is kept only
    when it is already inside the project. A drive-relative path, a UNC path,
    a rooted path, a different drive, and a backslash ``..`` are refused
    before this process resolves them, so a Windows path is not created as a
    odd relative file on another machine.
    """
    text = raw if isinstance(raw, str) else os.fspath(raw)
    root_resolved = Path(root).resolve()
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise error(f"{label} {raw} is outside the project folder {root_resolved}.")
    expanded = str(Path(text.strip()).expanduser())
    trimmed = _path_after_windows_trim(expanded)
    # Win32 drops trailing dots and spaces before it creates the file.
    # ``.. .`` is ``..`` after that, so the check uses the trimmed text.
    if trimmed != expanded.replace("\\", "/") and (
        session_path_escapes(trimmed) or _export_blocked(trimmed, root_resolved)
    ):
        raise error(f"{label} {raw} is outside the project folder {root_resolved}.")
    if _export_blocked(expanded, root_resolved):
        raise error(f"{label} {raw} is outside the project folder {root_resolved}.")
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()
    if not _is_inside(resolved, root_resolved):
        raise error(f"{label} {raw} is outside the project folder {root_resolved}.")
    return resolved


def _windows_trimmed_part(part: str) -> str:
    """One path part after Win32 removes trailing dots and spaces.

    ``.`` and ``..`` stay. ``.. .`` and ``...`` become ``..``, because the
    extra dots and spaces are removed and the parent part is what remains.
    """

    if part in {".", ".."}:
        return part
    trimmed = part
    while trimmed.endswith((" ", ".")) and trimmed not in {".", ".."}:
        trimmed = trimmed[:-1]
        if trimmed.endswith(" "):
            trimmed = trimmed.rstrip(" ")
    return trimmed


def _path_after_windows_trim(text: str) -> str:
    """The path Win32 keeps: each part loses its trailing dots and spaces."""

    return "/".join(_windows_trimmed_part(part) for part in text.replace("\\", "/").split("/"))


def _export_blocked(text: str, root: Path) -> bool:
    """Windows forms that must not be resolved or joined onto the project."""
    windows = PureWindowsPath(text)
    native = Path(text)
    if ".." in windows.parts and ".." not in native.parts:
        # ``foo\\..\\..\\out`` is one filename on POSIX and a climb on Windows.
        return True
    if windows.drive and not native.is_absolute():
        # ``C:name`` is the current directory on that drive, not this project.
        return True
    if windows.root and not native.is_absolute():
        # ``\\out`` or ``/out`` on Windows is the root of the current drive.
        return True
    if windows.is_absolute() and not native.is_absolute():
        # A drive letter or UNC share written into a session opened elsewhere.
        return True
    drive = windows.drive
    if drive.startswith("\\\\") or drive.startswith("//"):
        # UNC and ``\\?\`` paths. Do not resolve them; that can touch the network.
        return True
    if os.name == "nt" and native.is_absolute() and drive:
        root_drive = PureWindowsPath(os.fspath(root)).drive
        if root_drive and drive.lower() != root_drive.lower():
            return True
    return False


_SUPERSCRIPT_DIGITS = str.maketrans(
    {
        "\u00b9": "1",
        "\u00b2": "2",
        "\u00b3": "3",
        "\u2070": "0",
        "\u2074": "4",
        "\u2075": "5",
        "\u2076": "6",
        "\u2077": "7",
        "\u2078": "8",
        "\u2079": "9",
    }
)
_RESERVED_DEVICES = frozenset({"nul", "con", "prn", "aux"})


def is_reserved_device_name(component: str) -> bool:
    """True when Windows treats this file or folder name as a device.

    The name before the first dot or colon is what Windows uses, after trailing
    spaces and dots are removed. ``NUL.txt`` and ``COM¹`` count.
    """
    if not isinstance(component, str) or not component:
        return False
    text = component.translate(_SUPERSCRIPT_DIGITS).rstrip(" .")
    if not text or text in {".", ".."}:
        return False
    stem = text.split(".", 1)[0].split(":", 1)[0].rstrip(" .").casefold()
    if stem in _RESERVED_DEVICES:
        return True
    return len(stem) == 4 and stem[:3] in {"com", "lpt"} and stem[3] in "123456789"


def refuse_reserved_device(path: str | Path, root: Path | None, label: str, error: type[Exception]) -> None:
    """Refuse a Windows device name used as an output file or folder."""
    raw = Path(path)
    if root is not None and raw.is_absolute():
        try:
            parts = Path(os.path.normpath(raw)).relative_to(Path(os.path.normpath(root))).parts
        except ValueError:
            parts = (raw.name,)
    else:
        parts = tuple(part for part in raw.parts if part not in {os.sep, "/", "\\"})
    for part in parts:
        if is_reserved_device_name(part):
            raise error(f"{label} {part!r} is reserved on Windows.")


def restrict_private_file(path: Path) -> None:
    """Leave a secret file readable by this user only.

    On Windows the mode passed to ``open`` is not an access list. ``stat``
    then reports a writable file as mode ``0o666``. The access list is what
    keeps the file private.
    """

    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    _windows_set_dacl(path, "D:P(A;;FA;;;OW)")


def file_is_user_private(path: Path) -> bool:
    """True when the file is owner-read/write, with no group or other access."""

    if os.name != "nt":
        return path.stat().st_mode & 0o777 == 0o600
    text = _windows_dacl_text(path).upper()
    if any(marker in text for marker in (";WD)", ";BU)", ";AU)", ";BA)", ";WD,", ";BU,", ";AU,", ";BA,")):
        return False
    return "(A;" in text


def deny_directory_write(path: Path) -> None:
    """Take write access off one directory. The caller restores it."""

    if os.name != "nt":
        path.chmod(0o555)
        return
    _windows_set_dacl(path, "D:P(A;;FRFX;;;OW)")


def allow_directory_write(path: Path) -> None:
    """Give this user write access to a directory ``deny_directory_write`` locked."""

    if os.name != "nt":
        path.chmod(0o755)
        return
    _windows_set_dacl(path, "D:P(A;;FA;;;OW)")


def _windows_security():
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return advapi32, kernel32


def _windows_set_dacl(path: Path, sddl: str) -> None:
    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = _windows_security()
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise OSError("Could not build the file access list.")
    try:
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ):
            raise OSError("Could not read the file access list.")
        status = advapi32.SetNamedSecurityInfoW(
            str(path),
            1,
            0x4 | 0x80000000,
            None,
            None,
            dacl,
            None,
        )
        if status != 0:
            raise OSError(f"Could not set the file access list (Windows error {status}).")
    finally:
        kernel32.LocalFree(descriptor)


def _windows_dacl_text(path: Path) -> str:
    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = _windows_security()
    descriptor = ctypes.c_void_p()
    status = advapi32.GetNamedSecurityInfoW(
        str(path),
        1,
        0x4,
        None,
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if status != 0 or not descriptor:
        raise OSError(f"Could not read the file access list (Windows error {status}).")
    try:
        text = ctypes.c_void_p()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, 0x4, ctypes.byref(text), None
        ):
            raise OSError("Could not read the file access list.")
        try:
            return ctypes.wstring_at(text) if text else ""
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.LocalFree(descriptor)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
