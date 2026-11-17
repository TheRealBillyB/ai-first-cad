"""External programs aicad can use, and how to install them.

CalculiX (``ccx``) is GPL-2.0-only. aicad does not link it. It only starts
``ccx`` as a separate program, and only when a strength or vibration check asks
for it.

``ffmpeg`` writes an animation's MP4 or WebM. It is also a separate program:
one on PATH, or the build the optional ``imageio-ffmpeg`` package ships
(``pip install "aicad[video]"``, or ``python install.py --with-ffmpeg``).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SolverStatus:
    """Whether ``ccx`` can be started, and a message a person can act on."""

    found: bool
    path: str | None
    message: str


def ccx_hint(platform_name: str | None = None) -> str:
    """Plain-language install instructions for CalculiX on this operating system."""

    system = platform_name or sys.platform
    shared = (
        "CalculiX (`ccx`) is not on your PATH. Strength and vibration checks need it. "
        "Modeling, the app, print prep, and assemblies work without it.\n"
    )
    conda = "If you use conda, this works on Mac and Linux:\n    conda install conda-forge::calculix\n"
    pinned = "From your aicad folder, this downloads a pinned, checksummed build into .venv:\n    python install.py --with-calculix\n"
    if system == "win32":
        return (
            shared
            + pinned
            + "Do not put conda's Library\\bin folder on your PATH. powershell.exe stops starting, and aicad needs it."
        )
    if system == "darwin":
        return (
            shared
            + pinned
            + "Homebrew can also install it:\n"
            + "    brew install costerwi/homebrew-calculix/calculix-ccx\n"
            + conda
            + "Open a new terminal and run `ccx`."
        )
    return (
        shared
        + "On Ubuntu or Debian:\n"
        + "    sudo apt-get update && sudo apt-get install -y calculix-ccx\n"
        + "On Fedora, the package name is often `calculix`:\n"
        + "    sudo dnf install -y calculix\n"
        + conda
        + "Then run `ccx`."
    )


def bundled_ffmpeg(*, project: Path | None = None) -> str | None:
    """The ffmpeg program the optional ``imageio-ffmpeg`` package installed, or None.

    The package is found without importing it, and its own ``binaries`` folder
    is read. ``IMAGEIO_FFMPEG_EXE`` is not read, so a setting in the
    environment cannot name another program. A binary inside an open project
    is skipped, as on PATH.
    """

    try:
        import importlib.util

        spec = importlib.util.find_spec("imageio_ffmpeg")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    roots = _program_roots(project)
    for location in spec.submodule_search_locations:
        folder = Path(location) / "binaries"
        try:
            candidates = sorted(folder.iterdir())
        except OSError:
            continue
        for candidate in candidates:
            name = candidate.name.lower()
            if not name.startswith("ffmpeg"):
                continue
            if os.name == "nt" and not name.endswith(".exe"):
                continue
            if not _can_execute(candidate):
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if any(_inside(resolved, root) for root in roots):
                continue
            return str(resolved)
    return None


def ffmpeg_hint(platform_name: str | None = None) -> str:
    """Why no video was written, and the commands that add an encoder on this system."""

    system = platform_name or sys.platform
    shared = (
        "ffmpeg was not found, so no MP4 or WebM was written. The GIF is the animation. "
        "To write videos, add the bundled encoder to the Python that runs aicad: from the aicad folder, "
        "`python install.py --with-ffmpeg`, or `python -m pip install \"imageio-ffmpeg>=0.5\"`. "
    )
    if system == "win32":
        own = "Or install ffmpeg itself with `winget install Gyan.FFmpeg` and open a new terminal. "
    elif system == "darwin":
        own = "Or install ffmpeg itself with `brew install ffmpeg`. "
    else:
        own = (
            "Or install ffmpeg itself: `sudo apt-get install -y ffmpeg` on Ubuntu or Debian, "
            "or `sudo dnf install -y ffmpeg-free` on Fedora. "
        )
    return shared + own + "Then run this again."


# Windows searches these extensions when the name has none. An empty PATHEXT
# still uses this list, matching the usual command search.
_WIN_PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"


def find_program(name: str, *, project: Path | None = None) -> str | None:
    """Absolute path of ``name`` on PATH, outside any open project.

    The current directory is not searched. A PATH entry that is the project,
    or a directory inside it, is skipped. A path that resolves into the
    project is skipped too, so a link from outside the project is not the
    program that runs.
    """

    if not name or os.path.dirname(name):
        return None
    roots = _program_roots(project)
    for directory in _path_directories():
        if _directory_in_project(directory, roots):
            continue
        for candidate_name in _candidate_names(name):
            candidate = directory / candidate_name
            if not _can_execute(candidate):
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if any(_inside(resolved, root) for root in roots):
                continue
            return str(resolved)
    return None


def ccx_status() -> SolverStatus:
    """Look for the CalculiX solver, outside any open project."""

    found = find_program("ccx")
    if found:
        return SolverStatus(True, found, f"CalculiX is available ({found}).")
    return SolverStatus(False, None, ccx_hint())


def _program_roots(project: Path | None) -> tuple[Path, ...]:
    """Open projects, plus ``project`` when the caller named one.

    The installer loads this file by path, before the package is imported.
    With no package, there are no remembered projects, and the search still runs.
    """

    roots: list[Path] = []
    try:
        from aicad.paths import project_roots
    except ImportError:
        pass
    else:
        roots.extend(project_roots())
    if project is None:
        return tuple(roots)
    try:
        resolved = Path(project).resolve()
    except OSError:
        return tuple(roots)
    if resolved not in roots:
        roots.append(resolved)
    return tuple(roots)


def _path_directories():
    raw = os.environ.get("PATH", "")
    for entry in raw.split(os.pathsep):
        if entry == "" or entry == os.curdir:
            continue
        yield Path(entry)
    # Where `install.py --with-calculix` puts the solver. PATH comes first.
    prefix = Path(sys.prefix) / "calculix" / "prefix"
    yield prefix / "solver-bin"
    yield prefix / "bin"


def _candidate_names(name: str) -> list[str]:
    if os.name != "nt":
        return [name]
    source = os.environ.get("PATHEXT") or _WIN_PATHEXT
    extensions = [ext for ext in source.split(os.pathsep) if ext]
    lowered = name.lower()
    if any(lowered.endswith(ext.lower()) for ext in extensions):
        return [name]
    return [name + ext for ext in extensions]


def _can_execute(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _directory_in_project(directory: Path, roots: tuple[Path, ...]) -> bool:
    try:
        resolved = directory.resolve()
    except OSError:
        return True
    return any(_inside(resolved, root) for root in roots)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
