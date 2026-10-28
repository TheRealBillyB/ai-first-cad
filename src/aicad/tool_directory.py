"""A private directory for a helper program, opened by its handle.

The directory is created outside any project and opened before its path is
used. Reads, writes, and the helper's working directory use that open
directory. Replacing the directory's name does not point those operations
somewhere else.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import stat
import sys
from pathlib import Path


class HeldWork:
    """One private directory kept open until the caller finishes."""

    def __init__(self, original: Path, descriptor: int, windows_handle: int | None) -> None:
        self.original = original
        self.descriptor = descriptor
        self.windows_handle = windows_handle


@contextlib.contextmanager
def held_subprocess_directory():
    """Create a private directory and open it before the caller uses it."""
    from aicad.paths import _make_private_tool_directory, remove_tree

    original = _make_private_tool_directory()
    try:
        held = _hold(original)
    except Exception:
        remove_tree(original, ignore_errors=True)
        raise
    try:
        yield held
    finally:
        _close(held)


def held_run_kwargs(held: HeldWork) -> dict:
    """Arguments that run a program in ``held`` rather than at its former name."""
    if held.descriptor >= 0:
        proc = f"/proc/self/fd/{held.descriptor}"
        if os.path.isdir(proc):
            return {"cwd": proc, "close_fds": True}
        descriptor = held.descriptor

        def enter() -> None:
            os.fchdir(descriptor)

        return {"preexec_fn": enter, "pass_fds": (descriptor,), "close_fds": True}
    return {"cwd": os.fspath(held.original), "close_fds": True}


def held_write_bytes(held: HeldWork, name: str, data: bytes) -> None:
    """Replace one file inside ``held``. A link at that name is not opened."""
    _one_component(name)
    descriptor = _open_write(held, name)
    try:
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            offset += os.write(descriptor, view[offset:])
    finally:
        os.close(descriptor)


def held_read_bytes(held: HeldWork, name: str, *, maximum: int) -> bytes:
    """Read one regular file inside ``held``. A missing file or a link is empty."""
    _one_component(name)
    try:
        descriptor = _open_read(held, name)
    except OSError:
        return b""
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            return b""
        return os.read(descriptor, info.st_size)
    finally:
        os.close(descriptor)


def held_is_file(held: HeldWork, name: str) -> bool:
    """True when ``name`` is one regular file in ``held`` and not a link."""
    try:
        _one_component(name)
        info = _lstat(held, name)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def held_unlink(held: HeldWork, name: str) -> None:
    """Remove one name inside ``held``. A directory link's target stays."""
    _one_component(name)
    if held.descriptor >= 0:
        os.unlink(name, dir_fd=held.descriptor)
        return
    os.unlink(held.original / name)


def _hold(original: Path) -> HeldWork:
    if os.name == "nt":
        handle = _open_windows_directory(original)
        return HeldWork(original, -1, handle)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(os.fspath(original), flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(errno.ENOTDIR, "not a directory", os.fspath(original))
        return HeldWork(original, descriptor, None)
    except Exception:
        os.close(descriptor)
        raise


def _close(held: HeldWork) -> None:
    from aicad.paths import remove_tree

    info = None
    current: Path | None = None
    if held.descriptor >= 0:
        try:
            info = os.fstat(held.descriptor)
        except OSError:
            info = None
        current = _directory_from_handle(held.descriptor)
        os.close(held.descriptor)
        held.descriptor = -1
    if held.windows_handle:
        _close_windows_handle(held.windows_handle)
        held.windows_handle = None
    if current is not None and info is not None:
        try:
            found = os.lstat(current)
        except OSError:
            found = None
        if found is not None and stat.S_ISDIR(found.st_mode) and os.path.samestat(info, found):
            remove_tree(current, ignore_errors=True)
        try:
            if held.original.is_symlink():
                held.original.unlink()
        except OSError:
            pass
        return
    remove_tree(held.original, ignore_errors=True)


def _directory_from_handle(descriptor: int) -> Path | None:
    proc = f"/proc/self/fd/{descriptor}"
    try:
        if os.path.isdir(os.path.dirname(proc)):
            return Path(os.readlink(proc))
    except OSError:
        pass
    if sys.platform != "darwin":
        return None
    import fcntl

    # Python refuses an fcntl string longer than 1024 bytes. macOS PATH_MAX is 1024.
    buffer = ctypes.create_string_buffer(1024)
    command = getattr(fcntl, "F_GETPATH", 50)
    try:
        fcntl.fcntl(descriptor, command, buffer)
    except OSError:
        return None
    raw = buffer.raw.split(b"\x00", 1)[0]
    if not raw:
        return None
    return Path(os.fsdecode(raw))


def _one_component(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise OSError(errno.EINVAL, "invalid path component", name)


def _open_write(held: HeldWork, name: str) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if held.descriptor >= 0:
        return os.open(name, flags, 0o600, dir_fd=held.descriptor)
    return os.open(os.fspath(held.original / name), flags, 0o600)


def _open_read(held: HeldWork, name: str) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if held.descriptor >= 0:
        return os.open(name, flags, dir_fd=held.descriptor)
    return os.open(os.fspath(held.original / name), flags)


def _lstat(held: HeldWork, name: str):
    if held.descriptor >= 0:
        return os.lstat(name, dir_fd=held.descriptor)
    return os.lstat(held.original / name)


def _open_windows_directory(original: Path) -> int:
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
        if _windows_handle_is_reparse(kernel32, handle):
            raise OSError(errno.ELOOP, "is a link", os.fspath(original))
        kept = int(handle)
        handle = None
        return kept
    finally:
        if handle:
            _close_windows_handle(handle)


def _windows_handle_is_reparse(kernel32, handle) -> bool:
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


def _close_windows_handle(handle) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    close.restype = ctypes.c_int
    close(handle)
