"""Project path checks that mean the same thing on Windows and POSIX.

A session file can be written on one machine and opened on another. Drive
letters, UNC shares, rooted paths, and ``..`` are decided from the text, with
both Windows and POSIX rules, before this process joins the path onto the
project or touches the file.
"""

from __future__ import annotations

import os
import stat
import tempfile
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
    if _export_blocked(expanded, root_resolved):
        raise error(f"{label} {raw} is outside the project folder {root_resolved}.")
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()
    if not _is_inside(resolved, root_resolved):
        raise error(f"{label} {raw} is outside the project folder {root_resolved}.")
    return resolved


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


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _lexical_inside(path: Path, root: Path) -> bool:
    """True when ``path`` is inside ``root`` before links are followed."""
    try:
        Path(os.path.normcase(os.path.abspath(path))).relative_to(
            Path(os.path.normcase(os.path.abspath(root)))
        )
    except ValueError:
        return False
    return True


def is_reparse(path: Path) -> bool:
    """True for a symlink or a Windows directory junction."""
    try:
        if path.is_symlink():
            return True
    except OSError:
        return False
    checker = getattr(os.path, "isjunction", None)
    if checker is None:
        return False
    try:
        return bool(checker(os.fspath(path)))
    except (OSError, ValueError):
        return False


def stays_inside(root: Path, path: Path) -> bool:
    """True when ``path`` resolves inside ``root``."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return _is_inside(resolved, Path(root).resolve())


def link_leaves(root: Path, path: str | Path) -> bool:
    """True when a path inside ``root`` resolves outside it.

    Opening that path would leave the project by following a symlink or junction.
    A path that is already outside ``root`` is not this case: the caller asked
    for a different folder.
    """
    root_resolved = Path(root).resolve()
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = root_resolved / raw
    try:
        parent = raw.parent.resolve()
        resolved = raw.resolve()
    except OSError:
        return False
    return _is_inside(parent, root_resolved) and not _is_inside(resolved, root_resolved)


def project_link_escapes(path: str | Path) -> bool:
    """True when ``path`` follows a link out of a project that contains it.

    The project is the nearest ancestor with a real ``session.json``. A symlink
    the caller chose as the project folder itself is not this case.
    """
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    probe = raw.parent
    seen: set[str] = set()
    while True:
        key = os.path.normcase(os.path.abspath(probe))
        if key in seen:
            return False
        seen.add(key)
        marker = probe / "session.json"
        if is_reparse(marker):
            holds_project = False
        else:
            try:
                holds_project = marker.is_file()
            except OSError:
                holds_project = False
        if holds_project:
            return not stays_inside(probe, raw)
        parent = probe.parent
        if parent == probe:
            return False
        probe = parent


def resolve_in_project(root: Path, path: str | Path, label: str, error: type[Exception]) -> Path:
    """Resolved path that stays inside ``root``. Does not create it.

    A symlink or Windows junction is followed. A link that leaves the project
    is refused before the file is opened.
    """
    root_resolved = Path(root).resolve()
    raw = Path(path)
    if not raw.is_absolute():
        raw = root_resolved / raw
    if "\x00" in os.fspath(raw):
        raise error(f"{label} {raw} is outside the project folder {root_resolved}.")
    try:
        resolved = raw.resolve()
    except OSError as exc:
        raise error(f"{label} {raw} is outside the project folder {root_resolved}.") from exc
    if not _is_inside(resolved, root_resolved):
        raise error(f"{label} {raw} is outside the project folder {root_resolved}.")
    return resolved


def output_dir(root: Path, path: str | Path, label: str, error: type[Exception]) -> Path:
    """A real directory inside ``root``.

    Nothing is created when the path resolves outside the project. Missing
    directories inside the project are created.
    """
    root_resolved = Path(root).resolve()
    raw = Path(path)
    if not raw.is_absolute():
        raw = root_resolved / raw
    if not _lexical_inside(raw, root_resolved):
        raise error(f"{label} {path} is outside the project folder {root_resolved}.")
    try:
        resolved = raw.resolve()
    except OSError as exc:
        raise error(f"{label} {path} is outside the project folder {root_resolved}.") from exc
    if not _is_inside(resolved, root_resolved):
        raise error(f"{label} {path} is outside the project folder {root_resolved}.")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        resolved = resolved.resolve()
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise error(f"Could not create {label} {path}. {reason}.") from exc
    if not _is_inside(resolved, root_resolved):
        raise error(f"{label} {path} is outside the project folder {root_resolved}.")
    return resolved


def output_path(
    root: Path,
    path: str | Path,
    label: str,
    error: type[Exception],
    *,
    allow_absolute_outside: bool = False,
) -> Path:
    """A file path to create or replace without following a link out of ``root``.

    A relative path, and an absolute path that is lexically inside the project,
    must still be inside after links are followed. An absolute path that is
    already outside the project is returned unchanged when
    ``allow_absolute_outside`` is set, so a caller can write a file the person
    named explicitly.
    """
    root_resolved = Path(root).resolve()
    raw = Path(path)
    absolute = raw.is_absolute()
    if not absolute:
        raw = root_resolved / raw
    if "\x00" in os.fspath(raw):
        raise error(f"{label} {path} is outside the project folder {root_resolved}.")
    if not _lexical_inside(raw, root_resolved):
        if absolute and allow_absolute_outside:
            return Path(os.path.abspath(raw))
        raise error(f"{label} {path} is outside the project folder {root_resolved}.")
    try:
        resolved = raw.resolve()
    except OSError as exc:
        raise error(f"{label} {path} is outside the project folder {root_resolved}.") from exc
    if not _is_inside(resolved, root_resolved):
        raise error(f"{label} {path} is outside the project folder {root_resolved}.")
    parent = output_dir(root_resolved, resolved.parent, label, error)
    name = resolved.name
    if name in {"", ".", ".."}:
        raise error(f"{label} {path} is outside the project folder {root_resolved}.")
    return parent / name


def shared_file(path: Path) -> bool:
    """True when ``path`` is a regular file with another directory entry."""
    try:
        info = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_nlink > 1


def publish_file(path: Path, write) -> Path:
    """Write a temp file beside ``path``, then replace ``path``.

    Replacement does not follow a symlink and does not change a hard link's
    other name. ``path``'s parent must already be a real directory.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".aicad-", suffix=path.suffix, dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        write(temporary)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def publish_bytes(path: Path, data: bytes) -> Path:
    """Replace ``path`` with ``data`` without following a link at that name."""

    def write(temporary: Path) -> None:
        temporary.write_bytes(data)

    return publish_file(path, write)


def publish_text(path: Path, text: str) -> Path:
    """Replace ``path`` with UTF-8 text without following a link at that name."""
    return publish_bytes(path, text.encode("utf-8"))
