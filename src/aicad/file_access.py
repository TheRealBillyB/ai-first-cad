"""Open, read, and replace a file from a directory handle.

The directory is opened first. Each later component is opened from the
previous handle, and a link is not followed. The type, the link count, and
the size are taken from the opened file. The same steps run on Windows,
macOS, and Linux.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
import uuid
from pathlib import Path

_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
_OPEN_EXISTING = 3
_DIR_ACCESS = 0x1 | 0x2 | 0x4 | 0x20 | 0x80 | 0x100 | 0x10000 | 0x100000
_FILE_READ_ACCESS = 0x1 | 0x80 | 0x100000
_FILE_WRITE_ACCESS = 0x1 | 0x2 | 0x100 | 0x10000 | 0x100000
_FILE_OPEN = 1
_FILE_CREATE = 2
_FILE_DIRECTORY_FILE = 0x1
_FILE_NON_DIRECTORY_FILE = 0x40
_FILE_SYNCHRONOUS_IO_NONALERT = 0x20
_FILE_OPEN_REPARSE_POINT = 0x00200000
_OBJ_CASE_INSENSITIVE = 0x40
_STATUS_OBJECT_NAME_COLLISION = 0xC0000035
_FILE_RENAME_INFO_EX = 22
_FILE_DISPOSITION_INFO = 4
_FILE_RENAME_REPLACE_IF_EXISTS = 0x1
_FILE_RENAME_POSIX_SEMANTICS = 0x2


class TooLarge(Exception):
    """The opened file is larger than the caller's cap."""


def available() -> bool:
    """True when a directory can be opened without following a link."""
    if sys.platform == "win32":
        return _windows_available()
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        return False
    try:
        from aicad.paths import _libc

        _libc()
    except OSError:
        return False
    return True


def open_directory(path: Path) -> int:
    """Open one directory. A link is refused. The caller closes the descriptor."""
    if sys.platform == "win32":
        return _windows_open_root(Path(path))
    from aicad.paths import _posix_open_directory

    return _posix_open_directory(path)


def open_relative(root: Path, relative: str | Path, *, create: bool = False) -> int:
    """Open ``root / relative`` one component at a time. A link is refused."""
    if sys.platform == "win32":
        return _windows_open_relative(Path(root), relative, create=create)
    from aicad.paths import _posix_open_relative

    return _posix_open_relative(root, relative, create=create)


def replace_file(root: Path, relative: str | Path, write) -> None:
    """Replace ``root / relative`` without following a link in any component."""
    if sys.platform == "win32":
        _windows_replace(Path(root), relative, write)
        return
    from aicad.paths import _posix_replace_file

    _posix_replace_file(root, relative, write)


def read_from_root(root: Path, relative: Path, maximum: int) -> bytes:
    """Read a regular file from ``root``. A link is not followed."""
    if sys.platform == "win32":
        descriptor = _windows_open_file(Path(root), relative)
    else:
        from aicad.paths import _open_file_fallback, _open_file_relative

        if available():
            descriptor = _open_file_relative(root, relative)
        else:
            descriptor = _open_file_fallback(root, relative)
    try:
        return _read_descriptor(descriptor, maximum)
    finally:
        os.close(descriptor)


def read_file(path: str | Path, *, maximum: int) -> bytes:
    """Read one file from its parent directory. A link at that name is refused."""
    from aicad.paths import _before_nofollow, _finish_read

    file = Path(path)
    if not file.is_absolute():
        file = Path.cwd() / file
    parent = file.parent
    relative = Path(file.name)
    _before_nofollow(parent, relative)
    return _finish_read(parent, relative, maximum, file.name, OSError)


def read_text(path: str | Path, *, maximum: int, errors: str = "strict") -> str:
    """UTF-8 text from :func:`read_file`. Newlines match :meth:`pathlib.Path.read_text`."""
    data = read_file(path, maximum=maximum)
    text = data.decode("utf-8", errors=errors) if errors != "strict" else data.decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_tail(path: str | Path, maximum: int) -> bytes:
    """The last ``maximum`` bytes. A link is not followed. A longer file is not read in full."""
    from aicad.paths import _before_nofollow

    file = Path(path)
    if not file.is_absolute():
        file = Path.cwd() / file
    parent = file.parent
    relative = Path(file.name)
    _before_nofollow(parent, relative)
    if sys.platform == "win32":
        descriptor = _windows_open_file(parent, relative)
    else:
        from aicad.paths import _open_file_fallback, _open_file_relative

        if available():
            descriptor = _open_file_relative(parent, relative)
        else:
            descriptor = _open_file_fallback(parent, relative)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "not an ordinary file")
        if info.st_nlink > 1:
            raise OSError(errno.ELOOP, "has another name")
        if info.st_size > maximum:
            os.lseek(descriptor, info.st_size - maximum, os.SEEK_SET)
        return os.read(descriptor, maximum)
    finally:
        os.close(descriptor)


def read_prefix(path: str | Path, count: int, *, maximum: int) -> bytes:
    """The first ``count`` bytes. A file past ``maximum`` is refused before the read."""
    from aicad.paths import _before_nofollow

    file = Path(path)
    if not file.is_absolute():
        file = Path.cwd() / file
    parent = file.parent
    relative = Path(file.name)
    _before_nofollow(parent, relative)
    if sys.platform == "win32":
        descriptor = _windows_open_file(parent, relative)
    else:
        from aicad.paths import _open_file_fallback, _open_file_relative

        if available():
            descriptor = _open_file_relative(parent, relative)
        else:
            descriptor = _open_file_fallback(parent, relative)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "not an ordinary file")
        if info.st_nlink > 1:
            raise OSError(errno.ELOOP, "has another name")
        if info.st_size > maximum:
            raise TooLarge()
        return os.read(descriptor, count)
    finally:
        os.close(descriptor)


def _read_descriptor(descriptor: int, maximum: int) -> bytes:
    from aicad.paths import _TooLarge

    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise OSError(errno.EINVAL, "not an ordinary file")
    if info.st_nlink > 1:
        raise OSError(errno.ELOOP, "has another name")
    if info.st_size > maximum:
        raise _TooLarge()
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > maximum:
        raise _TooLarge()
    return data


def _check_component(name: str) -> None:
    if name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise OSError(errno.EINVAL, "invalid path component", name)
    if sys.platform == "win32" and ":" in name:
        raise OSError(errno.EINVAL, "invalid path component", name)


def _relative_parts(relative: str | Path) -> tuple[str, ...]:
    path = Path(relative)
    if path.is_absolute():
        raise OSError(errno.EINVAL, "path is absolute")
    parts: list[str] = []
    for part in path.parts:
        _check_component(part)
        parts.append(part)
    return tuple(parts)


def _windows_available() -> bool:
    try:
        ctypes.WinDLL("ntdll")
        return True
    except OSError:
        return False


def _windows_open_root(path: Path) -> int:
    handle = _create_file(os.fspath(path), _DIR_ACCESS, _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT)
    try:
        _refuse_reparse(handle, path.name or os.fspath(path))
        if not _attributes(handle) & _FILE_ATTRIBUTE_DIRECTORY:
            raise OSError(errno.ENOTDIR, "not a directory", os.fspath(path))
        return _descriptor(handle, os.O_RDONLY)
    except Exception:
        _close_handle(handle)
        raise


def _windows_open_relative(root: Path, relative: str | Path, *, create: bool) -> int:
    parts = _relative_parts(relative) if Path(relative).parts else ()
    current = _windows_open_root(root)
    for name in parts:
        try:
            nxt = _windows_open_child(current, name, create=create)
        except OSError:
            os.close(current)
            raise
        os.close(current)
        current = nxt
    return current


def _windows_open_child(parent: int, name: str, *, create: bool) -> int:
    _check_component(name)
    parent_handle = _os_handle(parent)
    try:
        handle = _nt_open(parent_handle, name, access=_DIR_ACCESS, directory=True, create=False)
    except OSError as exc:
        if exc.errno != errno.ENOENT or not create:
            raise
        try:
            handle = _nt_open(parent_handle, name, access=_DIR_ACCESS, directory=True, create=True)
        except OSError as created:
            if created.errno != errno.EEXIST:
                raise
            handle = _nt_open(parent_handle, name, access=_DIR_ACCESS, directory=True, create=False)
    try:
        _refuse_reparse(handle, name)
        if not _attributes(handle) & _FILE_ATTRIBUTE_DIRECTORY:
            raise OSError(errno.ENOTDIR, "not a directory", name)
        return _descriptor(handle, os.O_RDONLY)
    except Exception:
        _close_handle(handle)
        raise


def _windows_open_file(root: Path, relative: Path) -> int:
    parts = _relative_parts(relative)
    if not parts:
        raise OSError(errno.EINVAL, "path is empty")
    parent = Path(*parts[:-1]) if len(parts) > 1 else Path()
    directory = _windows_open_relative(root, parent, create=False)
    try:
        handle = _nt_open(_os_handle(directory), parts[-1], access=_FILE_READ_ACCESS, directory=False, create=False)
    finally:
        os.close(directory)
    try:
        _refuse_reparse(handle, parts[-1])
        links = _link_count(handle)
        if links > 1:
            raise OSError(errno.ELOOP, "has another name")
        if _attributes(handle) & _FILE_ATTRIBUTE_DIRECTORY:
            raise OSError(errno.EINVAL, "not an ordinary file")
        _refuse_unexpected_final(handle, root, parts)
        return _descriptor(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except Exception:
        _close_handle(handle)
        raise


def _windows_replace(root: Path, relative: str | Path, write) -> None:
    from aicad import paths

    parts = paths._relative_parts(relative)
    if not parts:
        raise OSError(errno.EINVAL, "path is empty")
    name = parts[-1]
    _check_component(name)
    parent = Path(*parts[:-1]) if len(parts) > 1 else Path()
    directory = _windows_open_relative(root, parent, create=True)
    temporary = f".aicad-{os.getpid()}-{uuid.uuid4().hex}{Path(name).suffix}"
    descriptor = -1
    staged: Path | None = None
    placed = False
    try:
        handle = _nt_open(
            _os_handle(directory),
            temporary,
            access=_FILE_WRITE_ACCESS,
            directory=False,
            create=True,
        )
        descriptor = _descriptor(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))
        target, staged = paths._writer_target(descriptor, Path(name).suffix)
        write(target)
        if staged is not None:
            paths._copy_into_descriptor(descriptor, staged)
        os.fsync(descriptor)
        _rename_into(directory, descriptor, name)
        placed = True
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        if descriptor >= 0 and not placed:
            _delete_open(descriptor)
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def _rename_into(directory: int, descriptor: int, name: str) -> None:
    """Replace ``name`` in ``directory`` with the open file. A link is not followed."""
    existing = _open_existing(_os_handle(directory), name)
    if existing is not None:
        try:
            if _attributes(existing) & _FILE_ATTRIBUTE_DIRECTORY and not _attributes(existing) & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError(errno.EISDIR, "is a directory", name)
            # The handle was opened on this name. Removing it removes the
            # directory entry, including a link, and does not change another name.
            _delete_handle(existing)
        finally:
            _close_handle(existing)
    if not _set_rename(descriptor, directory, name, replace=False):
        raise OSError(errno.EIO, "could not replace", name)


def _open_existing(parent, name: str):
    try:
        return _nt_open(parent, name, access=_FILE_WRITE_ACCESS, directory=False, create=False)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        if exc.errno in {errno.EISDIR, errno.ENOTDIR}:
            raise OSError(errno.EISDIR, "is a directory", name) from exc
        raise


def _refuse_reparse(handle, name: str) -> None:
    if _attributes(handle) & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError(errno.ELOOP, "is a link", name)


def _refuse_unexpected_final(handle, root: Path, parts: tuple[str, ...]) -> None:
    final = _final_path(handle)
    root_final = _final_path_of_path(root)
    expected = os.path.normcase(os.path.normpath(os.path.join(root_final, *parts)))
    if final != expected:
        raise OSError(errno.ELOOP, "is a link")


def _final_path_of_path(path: Path) -> str:
    handle = _create_file(os.fspath(path), _DIR_ACCESS, _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT)
    try:
        return _final_path(handle)
    finally:
        _close_handle(handle)


def _create_file(path: str, access: int, flags: int):
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
    handle = create(path, access, _FILE_SHARE_ALL, None, _OPEN_EXISTING, flags, None)
    if not handle or handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error) if error else "could not open", path)
    return handle


def _nt_open(parent, name: str, *, access: int, directory: bool, create: bool):
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatus),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    ntdll.NtCreateFile.restype = ctypes.c_long
    name_buffer = ctypes.create_unicode_buffer(name)
    unicode = _Unicode()
    unicode.Length = len(name) * 2
    unicode.MaximumLength = (len(name) + 1) * 2
    unicode.Buffer = ctypes.cast(name_buffer, ctypes.c_wchar_p)
    attributes = _ObjectAttributes()
    attributes.Length = ctypes.sizeof(attributes)
    attributes.RootDirectory = parent
    attributes.ObjectName = ctypes.pointer(unicode)
    attributes.Attributes = _OBJ_CASE_INSENSITIVE
    status_block = _IoStatus()
    handle = ctypes.c_void_p()
    options = _FILE_OPEN_REPARSE_POINT | _FILE_SYNCHRONOUS_IO_NONALERT
    options |= _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
    disposition = _FILE_CREATE if create else _FILE_OPEN
    status = int(
        ntdll.NtCreateFile(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(status_block),
            None,
            0x80,
            _FILE_SHARE_ALL,
            disposition,
            options,
            None,
            0,
        )
    )
    if status < 0 or not handle.value:
        raise _nt_error(status, name)
    return handle.value


def _nt_error(status: int, name: str) -> OSError:
    code = status & 0xFFFFFFFF
    mapping = {
        0xC0000034: errno.ENOENT,
        0xC000003A: errno.ENOENT,
        0xC0000033: errno.EINVAL,
        0xC0000022: errno.EACCES,
        0xC0000035: errno.EEXIST,
        0xC00000BA: errno.EISDIR,
        0xC0000103: errno.ENOTDIR,
    }
    if code == _STATUS_OBJECT_NAME_COLLISION:
        err = errno.EEXIST
    else:
        err = mapping.get(code, errno.EIO)
    return OSError(err, os.strerror(err), name)


class _Unicode(ctypes.Structure):
    _fields_ = (
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_wchar_p),
    )


class _ObjectAttributes(ctypes.Structure):
    _fields_ = (
        ("Length", ctypes.c_ulong),
        ("RootDirectory", ctypes.c_void_p),
        ("ObjectName", ctypes.POINTER(_Unicode)),
        ("Attributes", ctypes.c_ulong),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    )


class _IoStatus(ctypes.Structure):
    _fields_ = (("Status", ctypes.c_void_p), ("Information", ctypes.c_void_p))


class _FileTime(ctypes.Structure):
    """Two 32-bit fields. A 64-bit field would shift the link count."""

    _fields_ = (
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    )


class _FileInfo(ctypes.Structure):
    """BY_HANDLE_FILE_INFORMATION. The link count is at byte 40."""

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


def _file_info(handle) -> _FileInfo:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get = kernel32.GetFileInformationByHandle
    get.argtypes = [ctypes.c_void_p, ctypes.POINTER(_FileInfo)]
    get.restype = ctypes.c_int
    info = _FileInfo()
    if not get(handle, ctypes.byref(info)):
        raise OSError(errno.EIO, "could not read file information")
    return info


def _attributes(handle) -> int:
    return int(_file_info(handle).dwFileAttributes)


def _link_count(handle) -> int:
    return int(_file_info(handle).nNumberOfLinks)


def _final_path(handle) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.GetFinalPathNameByHandleW
    query.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    query.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(query(handle, buffer, len(buffer), 0))
    if length <= 0 or length >= len(buffer):
        raise OSError(errno.EIO, "could not read the file path")
    text = buffer.value
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(text))


def _descriptor(handle, flags: int) -> int:
    import msvcrt

    value = int(handle if isinstance(handle, int) else handle)
    return msvcrt.open_osfhandle(value, flags)


def _os_handle(descriptor: int):
    import msvcrt

    return msvcrt.get_osfhandle(descriptor)


def _close_handle(handle) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    close.restype = ctypes.c_int
    close(handle)


def _delete_open(descriptor: int) -> None:
    try:
        _delete_handle(_os_handle(descriptor))
    except OSError:
        return


def _delete_handle(handle) -> None:
    class _Disposition(ctypes.Structure):
        _fields_ = (("DeleteFile", ctypes.c_byte),)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    set_info.restype = ctypes.c_int
    info = _Disposition(1)
    if not set_info(handle, _FILE_DISPOSITION_INFO, ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError(errno.EIO, "could not remove")


def _set_rename(descriptor: int, directory: int, name: str, *, replace: bool) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    set_info.restype = ctypes.c_int
    flags = _FILE_RENAME_POSIX_SEMANTICS
    if replace:
        flags |= _FILE_RENAME_REPLACE_IF_EXISTS
    class _Rename(ctypes.Structure):
        _fields_ = (
            ("Flags", ctypes.c_uint32),
            ("RootDirectory", ctypes.c_void_p),
            ("FileNameLength", ctypes.c_uint32),
            ("FileName", ctypes.c_wchar * (len(name) + 1)),
        )

    info = _Rename()
    info.Flags = flags
    info.RootDirectory = _os_handle(directory)
    info.FileNameLength = len(name) * 2
    info.FileName = name
    return bool(set_info(_os_handle(descriptor), _FILE_RENAME_INFO_EX, ctypes.byref(info), ctypes.sizeof(info)))
