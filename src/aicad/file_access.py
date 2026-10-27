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
# A read asks only to list and traverse. Add and write are refused by a
# confined process that is still allowed to read the directory.
# DELETE is not requested. That right deletes the directory itself, and
# Windows refuses it while the directory is the working directory.
_DIR_READ = 0x1 | 0x20 | 0x80 | 0x100000
_DIR_ACCESS = _DIR_READ | 0x2 | 0x4 | 0x100
_FILE_READ_ACCESS = 0x1 | 0x80 | 0x100000
# FILE_READ_ATTRIBUTES (0x80) is required to read the file record on a write handle.
_FILE_WRITE_ACCESS = 0x1 | 0x2 | 0x80 | 0x100 | 0x10000 | 0x100000
_FILE_OPEN = 1
_FILE_CREATE = 2
# Open the name or create it in the same request. A second request would
# check the path and then create it, and the name could change in between.
_FILE_OPEN_IF = 3
_FILE_DIRECTORY_FILE = 0x1
_FILE_NON_DIRECTORY_FILE = 0x40
_FILE_SYNCHRONOUS_IO_NONALERT = 0x20
_FILE_OPEN_REPARSE_POINT = 0x00200000
_OBJ_CASE_INSENSITIVE = 0x40
_STATUS_OBJECT_NAME_COLLISION = 0xC0000035
_FILE_DISPOSITION_INFO = 4
_FILE_RENAME_POSIX_SEMANTICS = 0x2
_FILE_RENAME_INFORMATION = 10
_FILE_RENAME_INFORMATION_EX = 65
_VOLUME_NAME_DOS = 0
_VOLUME_NAME_GUID = 1
_VOLUME_NAME_NT = 2


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
        return _windows_open_relative(Path(root), relative, create=create, write=create)
    from aicad.paths import _posix_open_relative

    return _posix_open_relative(root, relative, create=create)


def open_inplace(path: str | Path) -> int:
    """Open an existing file so it can be rewritten. A link is refused.

    The caller closes the descriptor. The file is truncated.
    """

    file = Path(path)
    if sys.platform == "win32":
        return _windows_open_inplace(file)
    flags = os.O_WRONLY | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(file, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "not an ordinary file", str(file))
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def replace_file(root: Path, relative: str | Path, write, *, private: bool = False) -> None:
    """Replace ``root / relative`` without following a link in any component."""
    if sys.platform == "win32":
        _windows_replace(Path(root), relative, write, private=private)
        return
    from aicad.paths import _posix_replace_file

    _posix_replace_file(root, relative, write, private=private)


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


def _absolute_file(path: str | Path) -> Path:
    file = Path(path)
    if not file.is_absolute():
        file = Path.cwd() / file
    return file


def _marks_project(directory: Path) -> bool:
    """True when ``directory`` has a ``session.json`` name.

    A link at that name still marks the directory. The marker is not read.
    """
    from aicad.paths import is_reparse

    marker = directory / "session.json"
    try:
        if is_reparse(marker):
            return True
        return marker.is_file()
    except OSError:
        return False


def _session_project(file: Path) -> Path | None:
    """Nearest ancestor with ``session.json`` that is not reached through a link.

    A directory link is skipped. A marker found only by following that link
    does not make the link the project.
    """
    from aicad.paths import is_reparse

    chain: list[Path] = []
    probe = file.parent
    seen: set[str] = set()
    while True:
        key = os.path.normcase(os.path.abspath(probe))
        if key in seen:
            return None
        seen.add(key)
        chain.append(probe)
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    linked: list[bool] = []
    for candidate in chain:
        try:
            linked.append(is_reparse(candidate))
        except OSError:
            linked.append(True)
    ancestor_linked = [False] * len(chain)
    crossed = False
    for index in range(len(chain) - 1, -1, -1):
        ancestor_linked[index] = crossed
        if linked[index]:
            crossed = True
    for index, candidate in enumerate(chain):
        if linked[index] or ancestor_linked[index]:
            continue
        if _marks_project(candidate):
            return candidate
    return None


def _project_link_anchor(file: Path) -> tuple[Path, Path] | None:
    """A linked project directory, and the file's path under that link.

    The outermost directory link is the one closest to the root. When that
    link's target is a project, the file is opened from the target. A link
    under the project is a component of that open and is not followed.
    """
    from aicad.paths import _relative_within, is_reparse

    chain: list[Path] = []
    # abspath collapses "." and ".." and does not follow a directory link.
    # A parent segment is not a link, and the walk stops if it repeats.
    probe = Path(os.path.abspath(file.parent))
    seen: set[str] = set()
    while True:
        key = os.path.normcase(os.fspath(probe))
        if key in seen:
            break
        seen.add(key)
        chain.append(probe)
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    outermost: Path | None = None
    for candidate in reversed(chain):
        try:
            linked = is_reparse(candidate)
        except OSError:
            linked = True
        if linked:
            outermost = candidate
            break
    if outermost is None or not _marks_project(outermost):
        return None
    try:
        resolved = outermost.resolve()
        if is_reparse(resolved) or not resolved.is_dir():
            raise OSError(errno.ELOOP, "is a link")
        return resolved, _relative_within(outermost, file)
    except OSError as exc:
        raise OSError(errno.ELOOP, "is a link") from exc


def _read_anchor(file: Path) -> tuple[Path, Path]:
    """Directory to open, and the file's path under that directory.

    A file inside a project is opened from the project directory, so a
    directory link in the middle of the path is not followed. A project
    folder that is itself a link is opened from the directory it points at.
    Any other file is opened from its parent directory.
    """
    from aicad.paths import _lexical_inside, _project_containing, _relative_within, is_reparse

    holder = _project_containing(file)
    if holder is not None and _lexical_inside(file, holder):
        try:
            if not is_reparse(holder):
                return holder, _relative_within(holder, file)
        except OSError:
            pass
    found = _session_project(file)
    if found is not None:
        try:
            return found, _relative_within(found, file)
        except OSError:
            pass
    anchored = _project_link_anchor(file)
    if anchored is not None:
        return anchored
    return file.parent, Path(file.name)


def _open_read(file: Path) -> int:
    """Open ``file`` for reading. The caller closes the descriptor."""
    from aicad.paths import _before_nofollow, _open_file_fallback, _open_file_relative

    root, relative = _read_anchor(file)
    _before_nofollow(root, relative)
    if sys.platform == "win32":
        return _windows_open_file(root, relative)
    if available():
        return _open_file_relative(root, relative)
    return _open_file_fallback(root, relative)


def read_file(path: str | Path, *, maximum: int) -> bytes:
    """Read one file. A link in a project path is not followed."""
    from aicad.paths import _before_nofollow, _finish_read

    file = _absolute_file(path)
    root, relative = _read_anchor(file)
    _before_nofollow(root, relative)
    return _finish_read(root, relative, maximum, file.name, OSError)


def read_text(path: str | Path, *, maximum: int, errors: str = "strict") -> str:
    """UTF-8 text from :func:`read_file`. Newlines match :meth:`pathlib.Path.read_text`."""
    data = read_file(path, maximum=maximum)
    text = data.decode("utf-8", errors=errors) if errors != "strict" else data.decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_tail(path: str | Path, maximum: int) -> bytes:
    """The last ``maximum`` bytes. A link is not followed. A longer file is not read in full."""
    descriptor = _open_read(_absolute_file(path))
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
    descriptor = _open_read(_absolute_file(path))
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


def _windows_open_root(path: Path, *, write: bool = False) -> int:
    access = _DIR_ACCESS if write else _DIR_READ
    handle = _create_file(os.fspath(path), access, _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT)
    try:
        _refuse_reparse(handle, path.name or os.fspath(path))
        if not _attributes(handle) & _FILE_ATTRIBUTE_DIRECTORY:
            raise OSError(errno.ENOTDIR, "not a directory", os.fspath(path))
        return _descriptor(handle, os.O_RDONLY)
    except Exception:
        _close_handle(handle)
        raise


def _windows_open_relative(root: Path, relative: str | Path, *, create: bool, write: bool = False) -> int:
    parts = _relative_parts(relative) if Path(relative).parts else ()
    current = _windows_open_root(root, write=write)
    for name in parts:
        try:
            nxt = _windows_open_child(current, name, create=create, write=write)
        except OSError:
            os.close(current)
            raise
        os.close(current)
        current = nxt
    return current


def _windows_open_child(parent: int, name: str, *, create: bool, write: bool) -> int:
    _check_component(name)
    parent_handle = _os_handle(parent)
    access = _DIR_ACCESS if write else _DIR_READ
    # One request opens the name or creates it. A link at that name is the
    # object this handle refers to, and the caller refuses it.
    handle = _nt_open(
        parent_handle,
        name,
        access=access,
        directory=True,
        create=create,
        disposition=_FILE_OPEN_IF if create else _FILE_OPEN,
    )
    try:
        _refuse_reparse(handle, name)
        if not _attributes(handle) & _FILE_ATTRIBUTE_DIRECTORY:
            raise OSError(errno.ENOTDIR, "not a directory", name)
        return _descriptor(handle, os.O_RDONLY)
    except Exception:
        _close_handle(handle)
        raise


def _windows_open_inplace(path: Path) -> int:
    directory = open_directory(path.parent)
    handle = None
    descriptor = -1
    try:
        handle = _nt_open(
            _os_handle(directory),
            path.name,
            access=_FILE_WRITE_ACCESS,
            directory=False,
            create=False,
        )
        _refuse_reparse(handle, path.name)
        if _attributes(handle) & _FILE_ATTRIBUTE_DIRECTORY:
            raise OSError(errno.EINVAL, "not an ordinary file", str(path))
        descriptor = _descriptor(handle, os.O_WRONLY | getattr(os, "O_BINARY", 0))
        handle = None
        os.ftruncate(descriptor, 0)
        return descriptor
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        if handle is not None:
            _close_handle(handle)
        os.close(directory)


def _windows_open_file(root: Path, relative: Path) -> int:
    parts = _relative_parts(relative)
    if not parts:
        raise OSError(errno.EINVAL, "path is empty")
    parent = Path(*parts[:-1]) if len(parts) > 1 else Path()
    directory = _windows_open_relative(root, parent, create=False, write=False)
    handle = None
    try:
        handle = _nt_open(_os_handle(directory), parts[-1], access=_FILE_READ_ACCESS, directory=False, create=False)
        _refuse_reparse(handle, parts[-1])
        if _link_count(handle) > 1:
            raise OSError(errno.ELOOP, "has another name")
        if _attributes(handle) & _FILE_ATTRIBUTE_DIRECTORY:
            raise OSError(errno.EINVAL, "not an ordinary file")
        _refuse_unexpected_final(handle, _os_handle(directory), parts[-1])
        descriptor = _descriptor(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        handle = None
        return descriptor
    finally:
        if handle is not None:
            _close_handle(handle)
        os.close(directory)


def _windows_replace(root: Path, relative: str | Path, write, *, private: bool = False) -> None:
    from aicad import paths

    parts = paths._relative_parts(relative)
    if not parts:
        raise OSError(errno.EINVAL, "path is empty")
    name = parts[-1]
    _check_component(name)
    parent = Path(*parts[:-1]) if len(parts) > 1 else Path()
    directory = _windows_open_relative(root, parent, create=True, write=True)
    temporary = f".aicad-{os.getpid()}-{uuid.uuid4().hex}{Path(name).suffix}"
    descriptor = -1
    staged: Path | None = None
    placed = False
    security = None
    try:
        security_value = None
        if private:
            security, kernel32 = paths._owner_only_descriptor()
            security_value = int(security.value)
        handle = _nt_open(
            _os_handle(directory),
            temporary,
            access=_FILE_WRITE_ACCESS,
            directory=False,
            create=True,
            security=security_value,
        )
        if security is not None:
            held = security
            security = None
            kernel32.LocalFree(held)
        descriptor = _descriptor(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))
        target, staged = paths._writer_target(descriptor, Path(name).suffix)
        if private and staged is not None:
            paths.restrict_private_file(staged)
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
        if security is not None:
            kernel32.LocalFree(security)
            security = None


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
    _apply_rename(_os_handle(descriptor), _os_handle(directory), name)


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


def _refuse_unexpected_final(handle, parent_handle, name: str) -> None:
    """Refuse a file whose final path is not the opened directory plus its name.

    A drive-letter path is tried first. A confined process may only be able to
    return the NT path, so that form is tried with the same handles.
    """
    last: OSError | None = None
    for flag in (_VOLUME_NAME_DOS, _VOLUME_NAME_NT, _VOLUME_NAME_GUID):
        try:
            final = _final_path(handle, flag)
            parent = _final_path(parent_handle, flag)
        except OSError as exc:
            last = exc
            continue
        expected = os.path.normcase(os.path.normpath(os.path.join(parent, name)))
        if final != expected:
            raise OSError(errno.ELOOP, "is a link")
        return
    if last is not None:
        raise last
    raise OSError(errno.EIO, "could not read the file path")


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
        if error:
            raise OSError(None, None, path, error)
        raise OSError(errno.EIO, "could not open", path)
    return handle


def _nt_open(
    parent,
    name: str,
    *,
    access: int,
    directory: bool,
    create: bool,
    security=None,
    disposition: int | None = None,
):
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
    if security:
        attributes.SecurityDescriptor = int(security)
    status_block = _IoStatus()
    handle = ctypes.c_void_p()
    options = _FILE_OPEN_REPARSE_POINT | _FILE_SYNCHRONOUS_IO_NONALERT
    options |= _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
    if disposition is None:
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


def _final_path(handle, flag: int) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.GetFinalPathNameByHandleW
    query.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    query.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(query(handle, buffer, len(buffer), flag))
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


def _rename_information(root_handle: int, name: str, *, flags: int | None) -> bytes:
    """Native rename record. The directory handle is at byte 8 on 64-bit Windows."""
    encoded = name.encode("utf-16-le")
    buffer = bytearray(20 + len(encoded))
    if flags is not None:
        buffer[0:4] = (flags & 0xFFFFFFFF).to_bytes(4, "little")
    buffer[8:16] = (root_handle & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
    buffer[16:20] = len(encoded).to_bytes(4, "little")
    buffer[20:] = encoded
    return bytes(buffer)


def _apply_rename(file_handle, directory_handle, name: str) -> None:
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtSetInformationFile.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_IoStatus),
        ctypes.POINTER(ctypes.c_char),
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    ntdll.NtSetInformationFile.restype = ctypes.c_long
    attempts = (
        (_FILE_RENAME_INFORMATION, None),
        (_FILE_RENAME_INFORMATION_EX, _FILE_RENAME_POSIX_SEMANTICS),
    )
    seen: list[str] = []
    for info_class, flags in attempts:
        raw = _rename_information(int(directory_handle), name, flags=flags)
        buf = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
        status = int(
            ntdll.NtSetInformationFile(
                ctypes.c_void_p(int(file_handle)),
                ctypes.byref(_IoStatus()),
                buf,
                len(raw),
                info_class,
            )
        )
        if status >= 0:
            return
        seen.append(f"{status & 0xFFFFFFFF:08X}")
    raise OSError(errno.EIO, "could not replace (" + ", ".join(seen) + ")", name)
