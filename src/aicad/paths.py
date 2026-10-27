"""Project path checks that mean the same thing on Windows and POSIX.

A session file can be written on one machine and opened on another. Drive
letters, UNC shares, rooted paths, and ``..`` are decided from the text, with
both Windows and POSIX rules, before this process joins the path onto the
project or touches the file.
"""

from __future__ import annotations

import builtins
import contextlib
import ctypes
import ctypes.util
import errno
import importlib.machinery
import os
import re
import stat
import sys
import tempfile
import threading
import unicodedata
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from aicad.windows_directory import pin_interpreter_directory

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


def _config_roots() -> list[Path]:
    """Homes whose fixed config paths must not be opened."""
    roots: list[Path] = []
    try:
        roots.append(Path.home())
    except OSError:
        pass
    from aicad.sandbox_state import account_home

    remembered = account_home()
    if remembered is not None:
        roots.append(Path(remembered))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        roots.append(Path(xdg).expanduser())
    return roots


def _is_home_ini(path: Path) -> bool:
    """True for an ezdxf config reached through the account home or XDG config."""
    if path.name.lower() != "ezdxf.ini":
        return False
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return True
    try:
        if resolved == packaged_font_config().resolve():
            return False
    except OSError:
        pass
    for root in _config_roots():
        try:
            folder = root.resolve()
        except OSError:
            return True
        if resolved == folder or folder in resolved.parents:
            return True
    return False


def _filtered_config_files(original):
    def config_files():
        packaged = packaged_font_config()
        os.environ.setdefault("EZDXF_CONFIG_FILE", str(packaged))
        kept: list[Path] = []
        seen: set[str] = set()
        for item in original():
            path = Path(item)
            if _is_project_ini(path) or _is_home_ini(path):
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
                    candidate = Path(os.fspath(file))
                    if _is_project_ini(candidate) or _is_home_ini(candidate):
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


def _read_installed_source(origin: str) -> str:
    """Read the module the importer already found.

    This is the installed copy, not a project file. A confined process can
    read that copy without asking for a drive-letter path.
    """
    with open(origin, "rb") as handle:
        data = handle.read(1_000_001)
    if len(data) > 1_000_000:
        raise OSError(errno.EFBIG, "too large", origin)
    text = data.decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class _EzdxfOptionsLoader:
    def __init__(self, origin: str):
        self.origin = origin

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        source = _read_installed_source(self.origin)
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

    Those libraries read ``ezdxf.ini`` from the working directory and from the
    account home. A project is often that directory, and either file can name
    a font folder outside the project. ``EZDXF_CONFIG_FILE`` points at the file
    shipped with aicad. The working-directory file and the home file are not
    read. Variables that name a font, Gmsh, or OpenCascade configuration are
    removed before those libraries load. Matplotlib uses a private config
    directory. The image library asks to enter the interpreter directory while
    it loads. That request is refused, so a relative path in the project still
    opens.
    """
    from aicad.sandbox import scrub_library_environment

    scrub_library_environment()
    pin_interpreter_directory()
    global _FINDER_INSTALLED
    packaged = packaged_font_config()
    os.environ["EZDXF_CONFIG_FILE"] = str(packaged)
    if not _FINDER_INSTALLED:
        sys.meta_path.insert(0, _EzdxfOptionsFinder())
        _FINDER_INSTALLED = True
    module = sys.modules.get("ezdxf._options")
    if module is None or getattr(module, "options", None) is None:
        return
    _install_config_filter(module)
    loaded = list(module.options.loaded_config_files)
    packaged = packaged_font_config()
    if any(_outside_packaged_ini(Path(item), packaged) for item in loaded):
        _rebuild_options(module)


def _outside_packaged_ini(path: Path, packaged: Path) -> bool:
    try:
        return path.expanduser().resolve() != packaged.resolve()
    except OSError:
        return True


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
    """A session path that stays inside ``root``. The file is not opened here.

    A directory link is refused, including one whose target stays inside the
    project. Win32 removes trailing dots and spaces from each part before it
    opens the file. ``.. .`` is ``..`` after that, so the trimmed text is judged
    the same way an export path is.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise error(f"{label} is missing.")
    text = raw.strip()
    if "\x00" in text or session_path_escapes(text):
        raise error(f"{label} {raw!r} is outside the project. It was not read.")
    from aicad.labels import misleading_path

    if misleading_path(text):
        raise error(f"{label} {raw!r} is outside the project. It was not read.")
    trimmed = _path_after_windows_trim(text)
    if trimmed != text.replace("\\", "/") and session_path_escapes(trimmed):
        raise error(f"{label} {raw!r} is outside the project. It was not read.")
    refuse_windows_aliases(root, text, label, error)
    root_resolved = Path(root).resolve()
    lexical = root_resolved / text
    full = lexical.resolve()
    if not _is_inside(full, root_resolved):
        raise error(f"{label} {raw!r} is outside the project. It was not read.")
    # A directory link whose target stays inside the project is still a link.
    # The file opened is the path as written, the same as other project reads.
    if _reparse_between(root_resolved, lexical):
        raise error(f"{label} {raw!r} is a link and was not followed.")
    if shared_file(full):
        raise error(f"{label} {raw!r} has another name and was not opened.")
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
    from aicad.labels import misleading_path

    if misleading_path(text):
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
    refuse_short_name_components(text, label, error)
    if not Path(text).is_absolute():
        refuse_windows_aliases(root_resolved, text, label, error)
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


def windows_trim_escapes(text: str) -> bool:
    """True when Win32's trimmed form of ``text`` leaves a directory.

    ``..../..../hidden`` is ``../../hidden`` after trailing dots and spaces
    are removed. The untrimmed text can still look like a relative name.
    """

    normalized = text.replace("\\", "/")
    trimmed = _path_after_windows_trim(text)
    if trimmed == normalized:
        return False
    return session_path_escapes(trimmed)


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
_RESERVED_DEVICES = frozenset({"nul", "con", "prn", "aux", "conin$", "conout$"})


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


# The digit run is the generated suffix. Windows uses more than one digit
# once nine short names already share the stem, and the stem gets shorter.
_SHORT_NAME = re.compile(r"^[A-Za-z0-9]{1,6}~\d{1,6}(?:\.[A-Za-z0-9]{1,3})?$")


def refuse_short_name_components(text: str, label: str, error: type[Exception]) -> None:
    """Refuse an 8.3 short name, a trailing dot or space, or a stream name.

    The drive prefix of an absolute path is not a file name, so ``C:`` is
    kept. Every later component is checked, on every system.
    """

    raw = text.replace("\\", "/")
    if len(raw) > 180:
        raise error(f"{label} is too long for a project path.")
    windows = PureWindowsPath(text)
    parts = [part for part in raw.split("/") if part not in {"", ".", ".."}]
    if windows.drive and parts:
        drive = windows.drive.replace("\\", "/").strip("/")
        if parts[0].lower() == drive.lower() or parts[0].endswith(":"):
            parts = parts[1:]
    for part in parts:
        if ":" in part or _SHORT_NAME.match(part) or part.endswith((" ", ".")):
            raise error(f"{label} {part!r} is not a file name.")


def refuse_windows_aliases(root: Path, text: str, label: str, error: type[Exception]) -> None:
    """Refuse Windows path forms that name a different file than they show.

    Alternate data streams, 8.3 short names, trailing dots and spaces, and a
    path past the classic length limit are refused on every system. A name
    that differs only by case from an existing entry is the same file on a
    case-insensitive volume, so it is refused too. A name that is the same
    after Unicode normalization is the same file on a normalization-insensitive
    volume, so it is refused too.
    """

    raw = text.replace("\\", "/")
    if len(raw) > 180:
        raise error(f"{label} is too long for a project path.")
    parts = [part for part in raw.split("/") if part not in {"", ".", ".."}]
    for part in parts:
        if ":" in part or _SHORT_NAME.match(part) or part.endswith((" ", ".")):
            raise error(f"{label} {part!r} is not a file name.")
    current = Path(root).resolve()
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            return
        if not current.is_dir():
            return
        folded = part.casefold()
        normalized = unicodedata.normalize("NFC", part).casefold()
        try:
            entries = list(current.iterdir())
        except OSError:
            return
        match = None
        for entry in entries:
            if entry.name == part:
                match = entry
                break
            if entry.name.casefold() == folded:
                raise error(f"{label} {part!r} differs only by case from an existing name.")
            if unicodedata.normalize("NFC", entry.name).casefold() == normalized:
                raise error(f"{label} {part!r} matches an existing name.")
        current = match if match is not None else current / part


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


# Owner Rights only. The same list is applied when the file is created.
_OWNER_ONLY_SDDL = "D:P(A;;FA;;;OW)"


def _owner_only_descriptor():
    """A security descriptor for this user only. The caller frees it with LocalFree."""

    import ctypes

    advapi32, kernel32 = _windows_security()
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        _OWNER_ONLY_SDDL, 1, ctypes.byref(descriptor), None
    ):
        raise OSError("Could not build the file access list.")
    return descriptor, kernel32


def restrict_private_file(path: Path) -> None:
    """Leave a secret file readable by this user only.

    On Windows the mode passed to ``open`` is not an access list. ``stat``
    then reports a writable file as mode ``0o666``. The access list is what
    keeps the file private.
    """

    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    _windows_set_dacl(path, _OWNER_ONLY_SDDL)


def file_is_user_private(path: Path) -> bool:
    """True when the file is owner-read/write, with no group or other access."""

    if os.name != "nt":
        return path.stat().st_mode & 0o777 == 0o600
    text = _windows_dacl_text(path).upper()
    if any(marker in text for marker in (";WD)", ";BU)", ";AU)", ";BA)", ";WD,", ";BU,", ";AU,", ";BA,")):
        return False
    return "(A;" in text


# An Owner Rights allow is not this user's rights. Deny the user, Everyone,
# and Administrators directly. Traverse is included because a read-and-execute
# allow still let Windows create a folder. WRITE_DAC stays so the caller can
# restore the directory.
_FILE_ADD_FILE = 0x0002
_FILE_ADD_SUBDIRECTORY = 0x0004
_FILE_WRITE_EA = 0x0010
_FILE_TRAVERSE = 0x0020
_FILE_DELETE_CHILD = 0x0040
_FILE_WRITE_ATTRIBUTES = 0x0100
_WRITE_DAC = 0x00040000
_FILE_GENERIC_READ = 0x00120089
_DIRECTORY_LOCK_DENY = (
    _FILE_ADD_FILE
    | _FILE_ADD_SUBDIRECTORY
    | _FILE_WRITE_EA
    | _FILE_TRAVERSE
    | _FILE_DELETE_CHILD
    | _FILE_WRITE_ATTRIBUTES
)
_DIRECTORY_LOCK_ALLOW = _FILE_GENERIC_READ | _WRITE_DAC
_WIN_WORLD_SID = 1
_WIN_BUILTIN_ADMINISTRATORS_SID = 26
_WIN_BUILTIN_USERS_SID = 27


def deny_directory_write(path: Path) -> None:
    """Take write access off one directory. The caller restores it.

    A new file in that directory is refused.
    """

    if os.name != "nt":
        path.chmod(0o555)
        return
    _windows_lock_directory(path)


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


def _windows_lock_directory(path: Path) -> None:
    """Protected access list: this user cannot create a file or a folder."""

    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = _windows_security()
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.CreateWellKnownSid.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.CreateWellKnownSid.restype = wintypes.BOOL
    advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    advapi32.InitializeAcl.restype = wintypes.BOOL
    advapi32.AddAccessDeniedAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    advapi32.AddAccessDeniedAce.restype = wintypes.BOOL
    advapi32.AddAccessAllowedAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    advapi32.AddAccessAllowedAce.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError("Could not read the user for the directory lock.")
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        user = ctypes.create_string_buffer(max(int(needed.value), 8))
        if not advapi32.GetTokenInformation(token, 1, user, len(user), ctypes.byref(needed)):
            raise OSError("Could not read the user for the directory lock.")
        user_sid = ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0]
        if not user_sid:
            raise OSError("Could not read the user for the directory lock.")
        deny_sids = [ctypes.c_void_p(user_sid)]
        sid_buffers = []
        for kind in (_WIN_WORLD_SID, _WIN_BUILTIN_ADMINISTRATORS_SID, _WIN_BUILTIN_USERS_SID):
            buffer = ctypes.create_string_buffer(68)
            size = wintypes.DWORD(68)
            if not advapi32.CreateWellKnownSid(kind, None, buffer, ctypes.byref(size)):
                raise OSError("Could not build the directory lock.")
            sid_buffers.append(buffer)
            deny_sids.append(ctypes.cast(buffer, ctypes.c_void_p))
        acl = ctypes.create_string_buffer(512)
        if not advapi32.InitializeAcl(acl, len(acl), 2):
            raise OSError("Could not build the directory lock.")
        for sid in deny_sids:
            if not advapi32.AddAccessDeniedAce(acl, 2, _DIRECTORY_LOCK_DENY, sid):
                raise OSError("Could not build the directory lock.")
        if not advapi32.AddAccessAllowedAce(acl, 2, _DIRECTORY_LOCK_ALLOW, ctypes.c_void_p(user_sid)):
            raise OSError("Could not build the directory lock.")
        status = advapi32.SetNamedSecurityInfoW(
            str(path),
            1,
            0x4 | 0x80000000,
            None,
            None,
            ctypes.cast(acl, ctypes.c_void_p),
            None,
        )
        if status != 0:
            raise OSError(f"Could not set the file access list (Windows error {status}).")
    finally:
        kernel32.CloseHandle(token)


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


def _long_path(path: Path) -> Path:
    """The long form of a path. On Windows an 8.3 name expands."""

    if os.name != "nt":
        return path
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get = kernel32.GetLongPathNameW
        get.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get.restype = wintypes.DWORD
        text = os.fspath(path)
        size = int(get(text, None, 0))
        if size <= 0:
            return path
        buffer = ctypes.create_unicode_buffer(size)
        written = int(get(text, buffer, size))
        if written <= 0:
            return path
        return Path(buffer.value)
    except OSError:
        return path


def _is_inside(path: Path, root: Path) -> bool:
    try:
        _long_path(path).relative_to(_long_path(root))
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


# Win32 reparse tags. Python 3.12 has os.path.isjunction. The Windows CI
# image is Python 3.11, which does not, so the mount-point tag is read here.
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003


def _mount_point_tag(attributes: int, tag: int | None) -> bool:
    """True when the Win32 attributes are a directory junction."""
    if attributes == _INVALID_FILE_ATTRIBUTES or not attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    return tag == _IO_REPARSE_TAG_MOUNT_POINT


def _windows_mount_point(path: Path) -> bool:
    """True for a directory junction when os.path.isjunction is not available."""
    if os.name != "nt":
        return False
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attrs = kernel32.GetFileAttributesW
    get_attrs.argtypes = [wintypes.LPCWSTR]
    get_attrs.restype = wintypes.DWORD
    attributes = int(get_attrs(os.fspath(path)))
    if attributes == _INVALID_FILE_ATTRIBUTES or not attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    create = kernel32.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    create.restype = ctypes.c_void_p
    handle = create(
        os.fspath(path),
        0x80,
        0x1 | 0x2 | 0x4,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if not handle or handle == invalid:
        return False
    try:
        buffer = ctypes.create_string_buffer(16 * 1024)
        returned = wintypes.DWORD(0)
        control = kernel32.DeviceIoControl
        control.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        control.restype = wintypes.BOOL
        ok = control(handle, 0x000900A8, None, 0, buffer, len(buffer), ctypes.byref(returned), None)
        if not ok or returned.value < 4:
            return False
        tag = int.from_bytes(buffer.raw[:4], "little")
        return _mount_point_tag(int(attributes), tag)
    finally:
        close = kernel32.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = wintypes.BOOL
        close(handle)


def is_reparse(path: Path) -> bool:
    """True for a symlink or a Windows directory junction."""
    try:
        if path.is_symlink():
            return True
    except OSError:
        return False
    checker = getattr(os.path, "isjunction", None)
    if checker is not None:
        try:
            return bool(checker(os.fspath(path)))
        except (OSError, ValueError):
            return False
    return _windows_mount_point(path)


def _unlink_reparse(path: Path) -> None:
    """Remove a symlink or junction. The directory it points at stays."""
    try:
        if path.is_symlink():
            path.unlink()
            return
    except OSError:
        pass
    os.rmdir(path)


@contextlib.contextmanager
def private_tool_directory():
    """A new folder outside every project. It is removed when the caller finishes.

    A slicer, ffmpeg, and CalculiX run here, on a copy of their inputs. The
    process temporary directory is not used when that directory is inside a
    project. The caller passes only names from this folder.
    """
    path = _make_private_tool_directory()
    try:
        yield path
    finally:
        remove_tree(path, ignore_errors=True)


def _make_private_tool_directory() -> Path:
    for root in _tool_temp_roots():
        try:
            path = Path(tempfile.mkdtemp(prefix="aicad-tool-", dir=os.fspath(root)))
        except OSError:
            continue
        if _inside_any_project(path):
            remove_tree(path, ignore_errors=True)
            continue
        return path
    raise OSError(errno.ENOENT, "No folder outside a project is available for a helper program.")


def _tool_temp_roots():
    """Writable folders that are not inside a project."""
    seeds: list[Path] = []
    for key in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            seeds.append(Path(value))
    try:
        seeds.append(Path(tempfile.gettempdir()))
    except OSError:
        pass
    if os.name == "nt":
        system = os.environ.get("SYSTEMROOT") or os.environ.get("SystemRoot")
        if system:
            seeds.append(Path(system) / "Temp")
    else:
        seeds.extend((Path("/tmp"), Path("/var/tmp")))
    seen: set[str] = set()
    for seed in seeds:
        candidate = _directory_outside_projects(seed)
        if candidate is None:
            continue
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        yield candidate


def _directory_outside_projects(start: Path) -> Path | None:
    """``start`` itself, or the nearest ancestor that is not inside a project."""
    probe = start
    seen: set[str] = set()
    while True:
        key = os.path.normcase(os.path.abspath(probe))
        if key in seen:
            return None
        seen.add(key)
        try:
            outside = probe.is_dir() and not is_reparse(probe) and not _inside_any_project(probe)
        except OSError:
            outside = False
        if outside:
            return probe
        parent = probe.parent
        if parent == probe:
            return None
        probe = parent


def _inside_any_project(path: Path) -> bool:
    """True when ``path`` sits in a project folder."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if _project_holding(resolved) is not None:
        return True
    return _project_containing(resolved) is not None


def remove_tree(path: Path, *, ignore_errors: bool = False) -> None:
    """Remove a file or directory. A symlink or junction is not followed."""

    def remove(target: Path) -> None:
        if is_reparse(target):
            _unlink_reparse(target)
            return
        if target.is_dir():
            for child in list(target.iterdir()):
                remove(child)
            target.rmdir()
            return
        target.unlink()

    try:
        candidate = Path(path)
        if not candidate.exists() and not candidate.is_symlink():
            return
        remove(candidate)
    except OSError:
        if not ignore_errors:
            raise


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
    The check is the path as written, not the directory it resolves to: a link
    in the middle still counts when the folder under it is outside. A path that
    is already outside ``root`` is not this case: the caller asked for a
    different folder.
    """
    root_resolved = Path(root).resolve()
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = root_resolved / raw
    if not _lexical_inside(raw, root_resolved):
        return False
    try:
        resolved = raw.resolve()
    except OSError:
        return False
    return not _is_inside(resolved, root_resolved)


def _project_holding(start: Path) -> Path | None:
    """Nearest ancestor with a real ``session.json``.

    A marker that is itself a link does not count. The walk does not follow a
    directory link, so a project reached only through that link is not chosen.
    """
    probe = start
    seen: set[str] = set()
    while True:
        key = os.path.normcase(os.path.abspath(probe))
        if key in seen:
            return None
        seen.add(key)
        marker = probe / "session.json"
        if not is_reparse(marker):
            try:
                if marker.is_file():
                    return probe
            except OSError:
                pass
        parent = probe.parent
        if parent == probe:
            return None
        probe = parent


def project_link_escapes(path: str | Path) -> bool:
    """True when ``path`` follows a link out of a project that contains it.

    The project is the nearest ancestor with a real ``session.json``. A symlink
    the caller chose as the project folder itself is not this case. A folder
    under that link is the same case when the link leaves the project above it.
    """
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    probe = raw
    seen: set[str] = set()
    while True:
        key = os.path.normcase(os.path.abspath(probe))
        if key in seen:
            return False
        seen.add(key)
        if is_reparse(probe):
            project = _project_holding(probe.parent)
            try:
                target = probe.resolve()
            except OSError:
                return False
            if project is not None and not _is_inside(target, Path(project).resolve()):
                return True
        parent = probe.parent
        if parent == probe:
            return False
        probe = parent


def resolve_in_project(root: Path, path: str | Path, label: str, error: type[Exception]) -> Path:
    """Resolved path that stays inside ``root``. Does not create it.

    A symlink or Windows junction is followed. A link that leaves the project
    is refused before the file is opened. A regular file with another name is
    refused too, so the other name is not what gets read.
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
    if shared_file(resolved):
        raise error(f"{label} {raw} has another name and was not opened.")
    return resolved


_LIBC = None
_PROJECTS: set[Path] = set()
_LINK_ERRNOS = {errno.ELOOP, errno.ENOTDIR}


def published_path(path: str | Path) -> str:
    """Absolute path text. A symlink or junction in the path is not followed."""

    return os.path.abspath(path)


def ensure_real_directory(path: str | Path) -> Path:
    """Create a directory. Do not follow a symlink or a junction to do it.

    Each new component is created from a directory handle. When the project
    folder is a link, that handle is the directory the link points at. A link
    earlier in the path is not opened as its own component.
    """

    raw = Path(os.path.abspath(path))
    if not nofollow_writes():
        return _ensure_real_directory_by_path(path, raw)
    try:
        root, relative = _directory_create_anchor(raw)
        if relative.parts:
            descriptor = open_relative(root, relative, create=True)
        else:
            descriptor = open_directory(root)
    except OSError as exc:
        if exc.errno == errno.ENOTDIR and exc.strerror == "not a folder":
            raise OSError(errno.ENOTDIR, f"{path} is not a folder.") from exc
        if exc.errno in _LINK_ERRNOS or exc.strerror == "is a link":
            raise OSError(errno.ELOOP, f"{path} is a link and was not followed.") from exc
        if exc.errno == errno.ENOTDIR:
            raise OSError(errno.ENOTDIR, f"{path} is not a folder.") from exc
        raise
    os.close(descriptor)
    return raw


def _ensure_real_directory_by_path(path: str | Path, raw: Path) -> Path:
    """Create ``raw`` by path when a directory handle is not available."""

    current = Path(raw.anchor)
    relative = raw.relative_to(current)
    for part in relative.parts:
        current = current / part
        if is_reparse(current) or current.is_symlink():
            raise OSError(errno.ELOOP, f"{path} is a link and was not followed.")
        if current.exists():
            if not current.is_dir():
                raise OSError(errno.ENOTDIR, f"{path} is not a folder.")
            continue
        current.mkdir()
    return raw


def _directory_create_anchor(directory: Path) -> tuple[Path, Path]:
    """Directory handle to create from, and the remaining path under it.

    A real project directory is opened by its own path, so a link above it
    (``/var`` on macOS) is only a prefix of that open. A project folder that
    is a link is opened from the directory it points at. Otherwise the last
    real directory that already exists is opened, and only the missing tail
    is created. The filesystem root is not walked one component at a time.
    """

    holder = _project_holding(directory)
    if holder is not None:
        try:
            holder_is_link = is_reparse(holder)
        except OSError:
            holder_is_link = True
        if not holder_is_link:
            return holder, _relative_within(holder, directory)
    found = _linked_project_anchor(directory)
    if found is not None:
        return found
    return _missing_tail_anchor(directory)


def _linked_project_anchor(path: Path) -> tuple[Path, Path] | None:
    """Resolved project a directory link points at, and ``path`` under that link.

    The outermost link that marks a project is used. A link that is not a
    project does not hide it. ``None`` when the path has no project link.
    """

    from aicad.file_access import _marks_project

    probe = path
    seen: set[str] = set()
    chain: list[Path] = []
    while True:
        key = os.path.normcase(os.path.abspath(probe))
        if key in seen:
            break
        seen.add(key)
        chain.append(probe)
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    for candidate in reversed(chain):
        try:
            linked = is_reparse(candidate)
        except OSError:
            linked = True
        if not linked or not _marks_project(candidate):
            continue
        try:
            resolved = candidate.resolve()
            if is_reparse(resolved) or not resolved.is_dir():
                raise OSError(errno.ELOOP, "is a link")
            return resolved, _relative_within(candidate, path)
        except OSError as exc:
            raise OSError(errno.ELOOP, "is a link") from exc
    return None


def _missing_tail_anchor(directory: Path) -> tuple[Path, Path]:
    """Last real directory that already exists, and the components left to create.

    When that directory's own name is a symlink or junction, the new directory
    would be created in the link target. That is refused. A link only in an
    earlier component stays a prefix of the path that is opened.
    """

    current = Path(directory.anchor)
    missing: list[str] = []
    parts = directory.relative_to(current).parts
    for index, part in enumerate(parts):
        nxt = current / part
        try:
            nxt.lstat()
        except OSError:
            missing = list(parts[index:])
            break
        current = nxt
    try:
        if is_reparse(current) or current.is_symlink():
            raise OSError(errno.ELOOP, "is a link")
    except OSError as exc:
        if exc.errno == errno.ELOOP or exc.strerror == "is a link":
            raise
        raise OSError(errno.ELOOP, "is a link") from exc
    if current != Path(directory.anchor):
        try:
            is_dir = current.is_dir()
        except OSError as exc:
            raise OSError(errno.ENOTDIR, "not a folder") from exc
        if not is_dir:
            raise OSError(errno.ENOTDIR, "not a folder")
    return current, Path(*missing)


def refuse_link_ancestors(path: str | Path, label: str, error: type[Exception]) -> None:
    """Refuse when an existing component of ``path`` is a symlink or junction."""

    raw = Path(os.path.abspath(path))
    current = Path(raw.anchor)
    for part in raw.relative_to(current).parts:
        current = current / part
        if is_reparse(current) or current.is_symlink():
            raise error(f"{label} {path} is a link and was not followed.")
        if not current.exists():
            return


def nofollow_writes() -> bool:
    """True when a directory can be opened without following a link."""
    from aicad.file_access import available

    return available()


def remember_project(root: Path) -> None:
    """Remember an open project so a later write starts from that directory."""
    resolved = Path(root).resolve()
    if resolved.is_dir():
        _PROJECTS.add(resolved)


def project_roots() -> tuple[Path, ...]:
    """Projects opened in this process. The caller cannot change the stored set."""
    return tuple(_PROJECTS)


def _project_containing(path: Path) -> Path | None:
    """The longest remembered project that lexically contains ``path``."""
    if not path.is_absolute():
        return None
    best: Path | None = None
    for root in _PROJECTS:
        if _lexical_inside(path, root) and (best is None or len(root.parts) > len(best.parts)):
            best = root
    return best


def _before_nofollow(root: Path, relative: Path) -> None:
    """After a project path is accepted, before that path is opened.

    The open that follows uses a directory descriptor and does not follow a link.
    """
    return


def _libc():
    """C library calls for descriptor-relative opens. Python 3.12 has no ``os.openat``."""
    global _LIBC
    if _LIBC is not None:
        return _LIBC
    library = ctypes.util.find_library("c")
    if not library:
        raise OSError(errno.ENOTSUP, "no-follow directory open is not available")
    libc = ctypes.CDLL(library, use_errno=True)
    libc.openat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
    libc.openat.restype = ctypes.c_int
    libc.mkdirat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    libc.mkdirat.restype = ctypes.c_int
    libc.renameat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
    libc.renameat.restype = ctypes.c_int
    libc.unlinkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    libc.unlinkat.restype = ctypes.c_int
    _LIBC = libc
    return libc


def _call(result: int, name: str) -> int:
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), name)
    return result


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _check_component(name: str) -> None:
    if name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name:
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


def _relative_within(root: Path, path: Path) -> Path:
    """``path`` relative to ``root`` without following links."""
    relative = Path(os.path.normpath(os.path.relpath(os.fspath(path), os.fspath(root))))
    if relative == Path("."):
        return Path()
    if relative.is_absolute() or ".." in relative.parts:
        raise OSError(errno.EINVAL, "path leaves the project")
    _relative_parts(relative)
    return relative


def _fd_path(descriptor: int) -> Path:
    """A path that reopens this descriptor instead of looking the name up again."""
    proc = Path(f"/proc/self/fd/{descriptor}")
    if proc.parent.is_dir():
        return proc
    return Path(f"/dev/fd/{descriptor}")


def _reopen_accepts_create(path: Path) -> bool:
    """True when ``path`` can be opened again with a create flag.

    ``/dev/fd/N`` refuses that open, so a writer which creates its file is
    given a private file and the bytes are copied into the descriptor.
    Windows has no such path. ``Path`` turns ``/dev/fd/N`` into ``\\dev\\fd\\N``.
    """
    text = os.fspath(path).replace("\\", "/")
    if text.startswith("/dev/fd/"):
        return False
    return sys.platform != "win32"


def _copy_into_descriptor(descriptor: int, source: Path) -> None:
    """Replace the contents of ``descriptor`` with ``source``."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    with source.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                offset += os.write(descriptor, chunk[offset:])
    os.ftruncate(descriptor, os.lseek(descriptor, 0, os.SEEK_CUR))


def _writer_target(descriptor: int, suffix: str) -> tuple[Path, Path | None]:
    """A path for ``write`` and, when set, a private file to copy back."""
    direct = _fd_path(descriptor)
    if _reopen_accepts_create(direct):
        return direct, None
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    raw, name = tempfile.mkstemp(prefix=".aicad-", suffix=suffix)
    os.close(raw)
    staged = Path(name)
    return staged, staged


def open_directory(path: Path) -> int:
    """Open one directory. A symlink is refused. The caller closes the descriptor."""
    from aicad.file_access import open_directory as opened

    return opened(path)


def _posix_open_directory(path: Path) -> int:
    """Open one directory on POSIX. A symlink is refused."""
    if not nofollow_writes():
        raise OSError(errno.ENOTSUP, "no-follow directory open is not available")
    return os.open(os.fspath(path), _directory_flags())


def _open_child(dirfd: int, name: str, *, create: bool) -> int:
    """Open ``name`` in ``dirfd``. Does not close ``dirfd`` and does not follow a link."""
    _check_component(name)
    encoded = os.fsencode(name)
    libc = _libc()
    flags = _directory_flags()
    try:
        return _call(libc.openat(dirfd, encoded, flags, 0), name)
    except OSError as exc:
        if exc.errno != errno.ENOENT or not create:
            raise
    made = libc.mkdirat(dirfd, encoded, 0o755)
    if made < 0:
        err = ctypes.get_errno()
        if err != errno.EEXIST:
            raise OSError(err, os.strerror(err), name)
    else:
        # mkdirat's mode is not applied on every system, same as openat.
        # chmod through the parent descriptor so a directory created with no
        # search bit can still be given one before it is opened.
        _chmod_new_directory(dirfd, name)
    return _call(libc.openat(dirfd, encoded, flags, 0), name)


def _chmod_new_directory(dirfd: int, name: str) -> None:
    """Give a directory this process just created owner search and write.

    ``chmod`` through the parent descriptor does not need the new directory to
    be searchable. Some systems reject that for a directory, and then the
    directory is opened and ``fchmod`` is used on the descriptor instead.
    """
    try:
        os.chmod(name, 0o755, dir_fd=dirfd, follow_symlinks=False)
        return
    except NotImplementedError:
        pass
    except OSError as exc:
        if exc.errno not in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL}:
            raise
    descriptor = _call(_libc().openat(dirfd, os.fsencode(name), _directory_flags(), 0), name)
    try:
        os.fchmod(descriptor, 0o755)
    finally:
        os.close(descriptor)


def open_relative(root: Path, relative: str | Path, *, create: bool = False) -> int:
    """Open ``root / relative`` one component at a time from ``root``'s descriptor.

    Each component uses ``openat`` with ``O_NOFOLLOW`` and ``O_DIRECTORY``.
    A symlink is refused. The caller closes the returned descriptor.
    ``relative`` may be empty, which opens ``root`` itself.
    """
    from aicad.file_access import open_relative as opened

    return opened(root, relative, create=create)


def _posix_open_relative(root: Path, relative: str | Path, *, create: bool = False) -> int:
    """Open ``root / relative`` with ``openat``. A symlink is refused."""
    parts = _relative_parts(relative) if Path(relative).parts else ()
    current = open_directory(root)
    for name in parts:
        try:
            nxt = _open_child(current, name, create=create)
        except OSError:
            os.close(current)
            raise
        os.close(current)
        current = nxt
    return current


def replace_file(root: Path, relative: str | Path, write, *, private: bool = False) -> None:
    """Replace ``root / relative`` without following a link in any component.

    The project directory is opened once. Each directory component is opened
    from the previous descriptor. The new bytes go to a new file in the parent
    directory, and that name then replaces ``relative``. ``private`` creates
    that file readable by this user only, before any bytes are written.
    """
    from aicad.file_access import replace_file as replaced

    replaced(root, relative, write, private=private)


def _posix_replace_file(root: Path, relative: str | Path, write, *, private: bool = False) -> None:
    """Replace ``root / relative`` with ``renameat``. A symlink is not followed."""
    parts = _relative_parts(relative)
    if not parts:
        raise OSError(errno.EINVAL, "path is empty")
    name = parts[-1]
    parent = Path(*parts[:-1]) if len(parts) > 1 else Path()
    dirfd = open_relative(root, parent, create=True)
    temporary = f".aicad-{os.getpid()}-{uuid.uuid4().hex}{Path(name).suffix}"
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    staged: Path | None = None
    placed = False
    try:
        mode = 0o600 if private else 0o644
        descriptor = _call(_libc().openat(dirfd, os.fsencode(temporary), flags, mode), temporary)
        # openat's mode is not applied on every system, so set it on the descriptor
        # before any bytes are written.
        os.fchmod(descriptor, mode)
        target, staged = _writer_target(descriptor, Path(name).suffix)
        if private and staged is not None:
            os.chmod(staged, 0o600)
        write(target)
        if staged is not None:
            _copy_into_descriptor(descriptor, staged)
        os.fsync(descriptor)
        _call(
            _libc().renameat(dirfd, os.fsencode(temporary), dirfd, os.fsencode(name)),
            name,
        )
        placed = True
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        if descriptor >= 0 and not placed:
            _libc().unlinkat(dirfd, os.fsencode(temporary), 0)
        if descriptor >= 0:
            os.close(descriptor)
        os.close(dirfd)


class _TooLarge(Exception):
    """The opened file is larger than the caller's cap. Its bytes are not returned."""


def read_checked_bytes(
    root: Path,
    path: str | Path,
    *,
    maximum: int,
    label: str,
    error: type[Exception],
) -> bytes:
    """Read a regular file inside ``root``.

    The path is checked, then opened from ``root`` one component at a time.
    A link at that name is not followed. More than ``maximum`` bytes is refused.
    """
    root_resolved = Path(root).resolve()
    raw = Path(path)
    if not raw.is_absolute():
        raw = root_resolved / raw
    resolved = resolve_in_project(root_resolved, path, label, error)
    if not resolved.is_file():
        raise error(f"Could not read {label}.")
    try:
        relative = _relative_within(root_resolved, raw)
    except OSError as exc:
        raise error(f"Could not read {label}.") from exc
    _before_nofollow(root_resolved, relative)
    return _finish_read(root_resolved, relative, maximum, label, error)


def read_checked_text(
    root: Path,
    path: str | Path,
    *,
    maximum: int,
    label: str,
    error: type[Exception],
) -> str:
    """UTF-8 text from :func:`read_checked_bytes`.

    Newlines match :meth:`pathlib.Path.read_text`: ``\\r\\n`` and a lone ``\\r``
    become ``\\n``.
    """
    data = read_checked_bytes(root, path, maximum=maximum, label=label, error=error)
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise error(f"Could not read {label}.") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_nofollow_bytes(path: str | Path, *, maximum: int, label: str, error: type[Exception]) -> bytes:
    """Read ``path`` without following a link at that name.

    A file inside a project this process has opened is read from that project's
    directory. A project folder that is itself a link is opened from the
    directory it points at, so a nested link is not followed. Any other file
    is read from its parent directory.
    """
    file = Path(path)
    if not file.is_absolute():
        file = Path.cwd() / file
    holder = _project_containing(file)
    if holder is not None and _lexical_inside(file, holder):
        try:
            if not is_reparse(holder):
                return read_checked_bytes(holder, file, maximum=maximum, label=label, error=error)
        except OSError:
            pass
    from aicad.file_access import _read_anchor

    try:
        root, relative = _read_anchor(file)
    except OSError as exc:
        raise _read_error(label, error, exc) from exc
    _before_nofollow(root, relative)
    return _finish_read(root, relative, maximum, label, error)


def _finish_read(root: Path, relative: Path, maximum: int, label: str, error: type[Exception]) -> bytes:
    try:
        return _read_from_root(root, relative, maximum)
    except _TooLarge:
        raise error(f"{label} is too large.") from None
    except OSError as exc:
        raise _read_error(label, error, exc) from exc


def _read_error(label: str, error: type[Exception], exc: OSError) -> Exception:
    if exc.errno in _LINK_ERRNOS:
        if exc.strerror == "has another name":
            return error(f"{label} has another name and was not opened.")
        return error(f"{label} is a link and was not followed.")
    if exc.errno == errno.EINVAL and exc.strerror == "not an ordinary file":
        return error(f"{label} is not an ordinary file.")
    return error(f"Could not read {label}.")


def _read_from_root(root: Path, relative: Path, maximum: int) -> bytes:
    from aicad.file_access import read_from_root

    return read_from_root(root, relative, maximum)


def _open_file_relative(root: Path, relative: Path) -> int:
    """Open a file from ``root``. Does not follow a link. The caller closes it."""
    parts = _relative_parts(relative)
    if not parts:
        raise OSError(errno.EINVAL, "path is empty")
    parent = Path(*parts[:-1]) if len(parts) > 1 else Path()
    dirfd = open_relative(root, parent, create=False)
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return _call(_libc().openat(dirfd, os.fsencode(parts[-1]), flags, 0), parts[-1])
    finally:
        os.close(dirfd)


def _open_file_fallback(root: Path, relative: Path) -> int:
    """Open ``root / relative`` when a directory descriptor is not available."""
    raw = root.joinpath(*relative.parts)
    current = Path(root)
    for part in relative.parts:
        current = current / part
        if is_reparse(current):
            raise OSError(errno.ELOOP, "is a link")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(os.fspath(raw), flags)


def _accept_project_file(root: Path, path: Path) -> Path:
    """A relative file whose parent is inside ``root`` before it is opened."""
    raw = path if path.is_absolute() else root / path
    if not _lexical_inside(raw, root):
        raise OSError(errno.EINVAL, f"{path} is outside the project folder {root}.")
    parent = raw.parent
    if parent.exists() or parent.is_symlink():
        try:
            resolved = parent.resolve()
        except OSError as exc:
            raise OSError(errno.ELOOP, f"{path} is outside the project folder {root}.") from exc
        if not _is_inside(resolved, root):
            raise OSError(errno.ELOOP, f"{path} is outside the project folder {root}.")
    return _relative_within(root, raw)


def _reparse_between(root: Path, raw: Path) -> bool:
    """True when an existing component of ``raw`` under ``root`` is a link.

    Creating a directory through that component would follow it. A junction or
    symlink that stays inside the project is still a link.
    """
    try:
        relative = Path(os.path.normpath(os.fspath(raw))).relative_to(Path(os.path.normpath(os.fspath(root))))
    except ValueError:
        return False
    current = Path(root)
    for part in relative.parts:
        current = current / part
        if is_reparse(current):
            return True
        try:
            exists = current.exists()
        except OSError:
            return True
        if not exists:
            return False
    return False


def output_dir(root: Path, path: str | Path, label: str, error: type[Exception]) -> Path:
    """A real directory inside ``root``.

    Nothing is created when the path resolves outside the project. Missing
    directories inside the project are created. A symlink or junction in the
    path is not followed when this system cannot open directories without
    following links, including a link whose target stays inside the project.
    A link that leaves the project is still reported as outside the project.
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
    if not nofollow_writes() and _reparse_between(root_resolved, raw):
        raise error(f"Could not create {label} {path}. {path} is a link and was not followed.")
    remember_project(root_resolved)
    if nofollow_writes():
        try:
            relative = _relative_within(root_resolved, raw)
            _before_nofollow(root_resolved, relative)
            if relative.parts:
                descriptor = open_relative(root_resolved, relative, create=True)
            else:
                descriptor = open_directory(root_resolved)
            os.close(descriptor)
            resolved = raw.resolve()
        except OSError as exc:
            if exc.strerror == "is a link" or exc.errno == errno.ELOOP:
                raise error(
                    f"Could not create {label} {path}. {path} is a link and was not followed."
                ) from exc
            reason = exc.strerror or str(exc)
            raise error(f"Could not create {label} {path}. {reason}.") from exc
        if not _is_inside(resolved, root_resolved):
            raise error(f"{label} {path} is outside the project folder {root_resolved}.")
        return resolved
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
    must still be inside after links are followed. The parent directory is the
    path as written, so a link in that path is not followed, including a link
    that stays inside the project. An absolute path that is already outside
    the project is returned unchanged when ``allow_absolute_outside`` is set,
    so a caller can write a file the person named explicitly.
    """
    root_resolved = Path(root).resolve()
    raw = Path(path)
    absolute = raw.is_absolute()
    if not absolute:
        raw = root_resolved / raw
    if "\x00" in os.fspath(raw):
        raise error(f"{label} {path} is outside the project folder {root_resolved}.")
    if _lexical_inside(raw, root_resolved):
        try:
            relative = Path(os.path.normpath(raw)).resolve().relative_to(root_resolved).as_posix()
        except (OSError, ValueError):
            relative = None
        if relative is not None:
            from aicad.labels import misleading_path

            if misleading_path(relative):
                raise error(f"{label} {path} is outside the project folder {root_resolved}.")
            if windows_trim_escapes(relative) or session_path_escapes(relative):
                raise error(f"{label} {path} is outside the project folder {root_resolved}.")
            refuse_windows_aliases(root_resolved, relative, label, error)
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
    if not nofollow_writes() and _reparse_between(root_resolved, raw):
        raise error(f"Could not create {label} {path}. {path} is a link and was not followed.")
    # ``resolved`` has followed links. Creating that parent would make the
    # directory through a link that stays inside the project.
    parent = output_dir(root_resolved, raw.parent, label, error)
    name = raw.name
    if _refused_file_name(name) or _windows_reserved_punctuation(name):
        raise error(f"{label} {path} is outside the project folder {root_resolved}.")
    return parent / name


def _refused_file_name(name: str) -> bool:
    return name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name


def _windows_reserved_punctuation(name: str) -> bool:
    """True when Windows will not create this as an ordinary file name."""
    if os.name != "nt":
        return False
    return any(char in name for char in ':*?"<>|')


def shared_file(path: Path) -> bool:
    """True when ``path`` is a regular file with another directory entry."""
    try:
        info = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_nlink > 1


def publish_file(path: Path, write, *, root: Path | None = None, private: bool = False) -> Path:
    """Write a temp file beside ``path``, then replace ``path``.

    Replacement does not follow a symlink and does not change a hard link's
    other name. When ``path`` is inside a project, or the project folder is a
    link, this system opens that directory once and each component from that
    descriptor. ``path``'s parent must already be a real directory on systems
    that cannot do that.
    """
    path = Path(path)
    chosen = Path(root).resolve() if root is not None else _project_containing(path)
    lexical = path if path.is_absolute() else (chosen / path if chosen is not None else path)
    if nofollow_writes():
        anchored = _publish_anchor(lexical, chosen)
        if anchored is not None:
            root_dir, relative = anchored
            _before_nofollow(root_dir, relative)
            try:
                replace_file(root_dir, relative, write, private=private)
            except OSError as exc:
                if exc.errno in _LINK_ERRNOS or exc.strerror == "is a link":
                    raise OSError(exc.errno or errno.ELOOP, f"{path} is a link and was not followed.") from exc
                raise
            return path
    temporary = _create_beside(path.parent, path.suffix)
    try:
        if private:
            if os.name == "nt":
                restrict_private_file(temporary)
            else:
                os.chmod(temporary, 0o600)
        write(temporary)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _publish_anchor(path: Path, chosen: Path | None) -> tuple[Path, Path] | None:
    """Project directory and relative file for a handle-relative replace.

    A path that is lexically inside a remembered project uses that project.
    A path through a project folder that is a link uses the directory that
    link points at. An earlier link that is not a project does not hide it.
    ``None`` means this system should create the file by path.
    """

    if chosen is not None and _lexical_inside(path, chosen):
        return chosen, _accept_project_file(chosen, path)
    from aicad.file_access import _project_link_anchor

    try:
        found = _project_link_anchor(path)
        if found is None:
            found = _linked_project_anchor(path)
    except OSError as exc:
        raise OSError(errno.ELOOP, f"{path} is a link and was not followed.") from exc
    if found is None or not found[1].parts:
        return None
    return found


def _create_beside(directory: Path, suffix: str) -> Path:
    """A new file in ``directory``.

    A permission error is raised at once. Checking the directory again after
    that error can wait on a locked folder. The directory and the new file are
    created from directory handles when this system can open one.
    """

    if nofollow_writes():
        ensure_real_directory(directory)
        from aicad.file_access import create_file

        last: OSError | None = None
        for _ in range(128):
            name = f".aicad-{os.getpid()}-{uuid.uuid4().hex}{suffix}"
            try:
                descriptor = create_file(directory, name)
            except FileExistsError as exc:
                last = exc
                continue
            os.close(descriptor)
            return directory / name
        if last is not None:
            raise last
        raise OSError(errno.EEXIST, "could not create a temporary file")
    return _create_beside_by_path(directory, suffix)


def _create_beside_by_path(directory: Path, suffix: str) -> Path:
    """A new file in ``directory`` when a directory handle is not available."""

    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory.mkdir(parents=True, exist_ok=True)
    last: OSError | None = None
    for _ in range(128):
        candidate = directory / f".aicad-{os.getpid()}-{uuid.uuid4().hex}{suffix}"
        try:
            descriptor = os.open(os.fspath(candidate), flags, 0o600)
        except FileExistsError as exc:
            last = exc
            continue
        os.close(descriptor)
        return candidate
    if last is not None:
        raise last
    raise OSError(errno.EEXIST, "could not create a temporary file")


def publish_bytes(path: Path, data: bytes, *, root: Path | None = None, private: bool = False) -> Path:
    """Replace ``path`` with ``data`` without following a link at that name."""

    def write(temporary: Path) -> None:
        temporary.write_bytes(data)

    return publish_file(path, write, root=root, private=private)


def publish_text(path: Path, text: str, *, root: Path | None = None, private: bool = False) -> Path:
    """Replace ``path`` with UTF-8 text without following a link at that name."""
    return publish_bytes(path, text.encode("utf-8"), root=root, private=private)


def hide_project_path(text: str, root: str | Path) -> str:
    """Replace an absolute project path with a relative one.

    The project folder itself becomes ``.``. A file inside it keeps the path
    under that folder. Slash and backslash forms are both removed.
    """

    if not isinstance(text, str) or not text:
        return text
    try:
        folder = str(Path(root).resolve())
    except OSError:
        folder = str(Path(root))
    variants = {folder, folder.replace("\\", "/"), folder.replace("/", "\\")}
    cleaned = text
    for variant in sorted((item for item in variants if item), key=len, reverse=True):
        cleaned = _replace_path_prefix(cleaned, variant)
    return cleaned


def _replace_path_prefix(text: str, variant: str) -> str:
    folded = text.casefold() if os.name == "nt" else text
    needle = variant.casefold() if os.name == "nt" else variant
    pieces: list[str] = []
    index = 0
    while True:
        found = folded.find(needle, index)
        if found < 0:
            pieces.append(text[index:])
            break
        after = found + len(needle)
        boundary = after >= len(text) or text[after] in "/\\ \t\r\n\"'.,;:)]}>"
        if not boundary:
            pieces.append(text[index:after])
            index = after
            continue
        pieces.append(text[index:found])
        if after < len(text) and text[after] in "/\\":
            index = after + 1
            continue
        pieces.append(".")
        index = after
    return "".join(pieces)


prepare_geometry_import()
