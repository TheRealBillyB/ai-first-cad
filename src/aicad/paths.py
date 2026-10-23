"""Project path checks that mean the same thing on Windows and POSIX.

A session file can be written on one machine and opened on another. Drive
letters, UNC shares, rooted paths, and ``..`` are decided from the text, with
both Windows and POSIX rules, before this process joins the path onto the
project or touches the file.
"""

from __future__ import annotations

import builtins
import importlib.machinery
import os
import re
import sys
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath

_OPTIONS_ASSIGN = re.compile(r"(?m)^options\s*=\s*Options\(\)\s*$")
_BLOCK_PROJECT_INI = threading.local()
_FINDER_INSTALLED = False


def packaged_font_config() -> Path:
    """Configuration file shipped with aicad for the geometry libraries."""
    return Path(__file__).resolve().parent / "data" / "ezdxf.ini"


def _is_project_ini(path: Path) -> bool:
    """True for ``ezdxf.ini`` in the working directory, not the packaged file."""
    if path.name.lower() != "ezdxf.ini":
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return not path.is_absolute()
    if resolved == packaged_font_config():
        return False
    if not path.is_absolute():
        return True
    try:
        return resolved == (Path.cwd() / "ezdxf.ini").resolve()
    except OSError:
        return False


def _filtered_config_files(original):
    def config_files():
        packaged = packaged_font_config()
        os.environ.setdefault("EZDXF_CONFIG_FILE", str(packaged))
        kept: list[Path] = []
        seen: set[str] = set()
        for item in original():
            path = Path(item)
            if _is_project_ini(path):
                continue
            key = os.fspath(path)
            if key in seen:
                continue
            seen.add(key)
            kept.append(path)
        if str(packaged) not in seen:
            kept.append(packaged)
        return kept

    return config_files


def _install_config_filter(module) -> None:
    if getattr(module, "_aicad_font_config", False):
        return
    module.config_files = _filtered_config_files(module.config_files)
    module._aicad_font_config = True


def _rebuild_options(module) -> None:
    fresh = module.Options()
    current = module.options
    current._loaded_paths = list(fresh._loaded_paths)
    current._config = fresh._config
    current.update_cached_options()


class _ProjectIniUnread:
    """Skip a project ``ezdxf.ini`` on this thread without moving other threads."""

    def __enter__(self):
        self._open = builtins.open

        def guarded(file, *args, **kwargs):
            if getattr(_BLOCK_PROJECT_INI, "on", False):
                try:
                    if _is_project_ini(Path(os.fspath(file))):
                        raise FileNotFoundError(os.fspath(file))
                except (TypeError, ValueError):
                    pass
            return self._open(file, *args, **kwargs)

        builtins.open = guarded
        _BLOCK_PROJECT_INI.on = True
        return self

    def __exit__(self, *exc):
        _BLOCK_PROJECT_INI.on = False
        builtins.open = self._open
        return False


class _EzdxfOptionsLoader:
    def __init__(self, origin: str):
        self.origin = origin

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        source = Path(self.origin).read_text(encoding="utf-8")
        replaced, count = _OPTIONS_ASSIGN.subn("options = None", source, count=1)
        code = compile(replaced if count == 1 else source, self.origin, "exec")
        with _ProjectIniUnread():
            exec(code, module.__dict__)
            _install_config_filter(module)
            if count == 1:
                module.options = module.Options()
            else:
                _rebuild_options(module)


class _EzdxfOptionsFinder:
    def find_spec(self, fullname, path, target=None):
        if fullname != "ezdxf._options" or fullname in sys.modules:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or not spec.origin:
            return None
        spec.loader = _EzdxfOptionsLoader(spec.origin)
        return spec


def prepare_geometry_import() -> None:
    """Use the packaged font config before the geometry libraries are imported.

    Those libraries read ``ezdxf.ini`` from the working directory. A project
    is often that directory, and the file can name a font folder outside the
    project. ``EZDXF_CONFIG_FILE`` points at the file shipped with aicad, and
    the working-directory file is not read. The process working directory is
    not changed, so another thread can keep opening relative paths.
    """
    global _FINDER_INSTALLED
    packaged = packaged_font_config()
    current = os.environ.get("EZDXF_CONFIG_FILE", "")
    if not current or _is_project_ini(Path(current)):
        os.environ["EZDXF_CONFIG_FILE"] = str(packaged)
    if not _FINDER_INSTALLED:
        sys.meta_path.insert(0, _EzdxfOptionsFinder())
        _FINDER_INSTALLED = True
    module = sys.modules.get("ezdxf._options")
    if module is None or getattr(module, "options", None) is None:
        return
    _install_config_filter(module)
    loaded = list(module.options.loaded_config_files)
    if any(_is_project_ini(Path(item)) for item in loaded):
        _rebuild_options(module)


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


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


prepare_geometry_import()
