"""Install aicad on Windows, macOS, and Linux.

From the project folder:

    python install.py

On Windows, ``py install.py`` if ``python`` is not on PATH. On a Mac,
``python3 install.py`` if that is the Python 3.11+ command.

The command creates ``.venv``, installs aicad into it, and looks for CalculiX
(``ccx``). On Ubuntu or Debian it also installs the system libraries when
``sudo`` works without a password. CalculiX is not downloaded. If ``ccx`` is
missing, the command prints how to install it on this operating system.
"""

from __future__ import annotations

import argparse
import ctypes.util
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 11)
# apt and pip can sit on a lock. A hung command has to end.
_INSTALL_TIMEOUT_SECONDS = 30 * 60

# Runtime libraries the OpenCascade wheel loads on Debian and Ubuntu, plus the
# CalculiX solver. build-essential and python3-dev are there so python-solvespace
# can be compiled when no wheel is published for this Python. The package names
# match the ones the test workflow used.
APT_PACKAGES = (
    "calculix-ccx",
    "build-essential",
    "python3-dev",
    "python3-venv",
    "libgl1",
    "libglu1-mesa",
    "libglib2.0-0",
    "libx11-6",
    "libxext6",
    "libxrender1",
    "libxcursor1",
    "libxfixes3",
    "libxft2",
    "libxinerama1",
    "libfontconfig1",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a virtual environment and install aicad. "
            "Also checks for CalculiX (ccx) and prints how to install it."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not install anything. Say whether aicad and CalculiX are ready.",
    )
    parser.add_argument(
        "--no-system",
        action="store_true",
        help="Do not try to install Ubuntu or Debian system packages.",
    )
    parser.add_argument(
        "--require-ccx",
        action="store_true",
        help="With --check, exit 1 when CalculiX is missing.",
    )
    args = parser.parse_args(argv)
    hints = _hints()
    if args.check:
        return _check(hints, require_ccx=args.require_ccx)
    if sys.version_info < MIN_PYTHON:
        print(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required. "
            f"This one is {sys.version.split()[0]}."
        )
        return 1
    system_ok = True
    if not args.no_system:
        system_ok = _install_linux_packages()
    python = _ensure_venv()
    for problem in _missing_prerequisites():
        print(problem)
        print()
    _pip_install(python)
    print()
    print(_ccx_message(hints))
    print()
    print(_ready_message(python))
    if not system_ok:
        print()
        print(
            "The system packages were not installed (see the apt-get message above). "
            "Some features, such as meshing and the stress check, may not work until they are."
        )
    return 0


def _hints():
    """Load the hint text without importing the geometry stack."""

    path = ROOT / "src" / "aicad" / "prereqs.py"
    spec = importlib.util.spec_from_file_location("aicad_prereqs", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not read {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclass looks the class up in sys.modules while the file is loading.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _venv_python() -> Path:
    if sys.platform == "win32":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _python_is_new_enough(python: Path) -> bool:
    major, minor = MIN_PYTHON
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info >= "
            f"({major}, {minor}) else 1)",
        ],
        check=False,
        capture_output=True,
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    return completed.returncode == 0


def _ensure_venv() -> Path:
    python = _venv_python()
    if python.is_file():
        if not _python_is_new_enough(python):
            major, minor = MIN_PYTHON
            raise SystemExit(
                f"The virtual environment at {python.parent.parent} is older than Python "
                f"{major}.{minor}. Remove .venv and run this installer again."
            )
        print(f"Using the existing virtual environment at {python.parent.parent}")
        return python
    print("Creating .venv")
    subprocess.run(
        [sys.executable, "-m", "venv", str(ROOT / ".venv")],
        cwd=ROOT,
        check=True,
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    if not python.is_file():
        raise SystemExit(f"The virtual environment was created, but {python} is missing.")
    return python


# C23 removed ATOMIC_VAR_INIT. These spellings select that language.
_C23_STANDARD = re.compile(r"-std=(?:gnu23|c23|gnu2x|c2x)(?![\w.])")


def _compiler_needs_c11(env: dict[str, str]) -> bool:
    """True when this compiler builds the solver's C files as GCC or Clang would.

    MSVC does not read ``CFLAGS``. Leaving it unset avoids passing ``-std``
    to ``cl``. MinGW and Clang on Windows do read it.
    """

    if sys.platform != "win32":
        return True
    compiler = env.get("CC", "").lower()
    return any(name in compiler for name in ("gcc", "clang", "mingw"))


def _c11_flags(flags: str) -> str:
    """CFLAGS that compile the solver as C11.

    An explicit C11 or C17 choice is kept. A C23 choice is replaced, because
    that language no longer provides the macro the solver uses.
    """

    if _C23_STANDARD.search(flags):
        return _C23_STANDARD.sub("-std=gnu11", flags)
    if "-std=" not in flags:
        return (flags + " -std=gnu11").strip()
    return flags


def _pip_env() -> dict[str, str]:
    """Environment for the pip install that compiles python-solvespace.

    That package ships a C11 mimalloc. It initializes an atomic with
    ATOMIC_VAR_INIT, which C23 removed. Current GCC defaults to C23, so the
    C files are compiled as C11 unless the caller already chose C11 or C17.
    """
    env = os.environ.copy()
    if not _compiler_needs_c11(env):
        return env
    env["CFLAGS"] = _c11_flags(env.get("CFLAGS", ""))
    return env


def _pip_install(python: Path) -> None:
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=ROOT,
        check=True,
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    if (ROOT / "pyproject.toml").is_file():
        print("Installing aicad and the development tools into .venv")
        subprocess.run(
            [str(python), "-m", "pip", "install", "-e", ".[dev,robot]"],
            cwd=ROOT,
            check=True,
            env=_pip_env(),
            timeout=_INSTALL_TIMEOUT_SECONDS,
        )
        return
    print("Installing aicad into .venv")
    subprocess.run(
        [str(python), "-m", "pip", "install", "aicad"],
        cwd=ROOT,
        check=True,
        env=_pip_env(),
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )


def _install_linux_packages() -> bool:
    """Install the Debian or Ubuntu system packages when sudo allows it.

    Returns False when apt-get ran and failed. A failure is reported with the
    command to run by hand; it does not stop the Python install.
    """

    if not sys.platform.startswith("linux"):
        return True
    if shutil.which("apt-get") is None:
        print("This Linux does not use apt. Install CalculiX yourself if the check below asks for it.")
        return True
    if not _passwordless_sudo():
        print("Skipping system packages because sudo needs a password. The command is:")
        print("    sudo apt-get update && sudo apt-get install -y " + " ".join(APT_PACKAGES))
        return True
    print("Installing system libraries and CalculiX (sudo apt-get).")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    commands = (
        ["sudo", "-n", "apt-get", "update"],
        ["sudo", "-n", "apt-get", "install", "-y", "--no-install-recommends", *APT_PACKAGES],
    )
    for command in commands:
        try:
            subprocess.run(
                command,
                cwd=ROOT,
                check=True,
                env=env,
                timeout=_INSTALL_TIMEOUT_SECONDS,
            )
        except subprocess.CalledProcessError as error:
            print(_apt_failed_message(command, f"exited with status {error.returncode}"))
            return False
        except subprocess.TimeoutExpired:
            print(
                _apt_failed_message(
                    command, f"did not finish within {_INSTALL_TIMEOUT_SECONDS // 60} minutes"
                )
            )
            return False
        except OSError as error:
            print(_apt_failed_message(command, f"could not start ({error})"))
            return False
    return True


def _apt_failed_message(command: list[str], reason: str) -> str:
    """What to do when apt-get fails. aicad itself can still be installed."""

    return "\n".join(
        (
            "",
            f"The system package step failed: `{' '.join(command)}` {reason}.",
            "The apt output above says why. Common causes are a broken or unreachable",
            "package source in /etc/apt/sources.list.d, no network, or another program",
            "holding the apt lock.",
            "The installer continues with the Python packages. To finish the system step,",
            "fix the apt problem and run:",
            "    sudo apt-get update && sudo apt-get install -y " + " ".join(APT_PACKAGES),
            "Then run `python install.py --check`. To skip this step next time, run",
            "`python install.py --no-system`.",
            "",
        )
    )


def _passwordless_sudo() -> bool:
    if shutil.which("sudo") is None:
        return False
    completed = subprocess.run(
        ["sudo", "-n", "true"],
        check=False,
        capture_output=True,
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    return completed.returncode == 0


def _ready_message(python: Path) -> str:
    """How to start aicad after install. The console command, or python -P."""

    command = python.with_name("aicad.exe" if sys.platform == "win32" else "aicad")
    if sys.platform == "win32":
        activate = r".\.venv\Scripts\Activate.ps1"
    else:
        activate = "source .venv/bin/activate"
    return "\n".join(
        (
            "aicad is installed. Turn the environment on, then try `aicad --help`.",
            f"    {activate}",
            f"Or run `{command}` without activating it.",
            "Do not run `python -m aicad` from a project folder. For development, use `python -P -m aicad`.",
        )
    )


def _ccx_message(hints) -> str:
    return hints.ccx_status().message


def _check(hints, *, require_ccx: bool) -> int:
    python = _venv_python()
    if not python.is_file():
        print("aicad is not installed yet. From this folder, run: python install.py")
        print()
        print(hints.ccx_hint())
        return 1
    # -I ignores the working directory and PYTHONPATH. This check is the
    # copy installed in the virtual environment, not a folder named aicad
    # next to install.py.
    imported = subprocess.run(
        [str(python), "-I", "-c", "import aicad; print('aicad', aicad.__version__)"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    if imported.returncode != 0:
        print(imported.stdout, end="")
        print(imported.stderr, end="", file=sys.stderr)
        print("aicad did not import from the virtual environment.")
        return 1
    print(imported.stdout.strip())
    for problem in _missing_prerequisites(build=False):
        print(problem)
    status = hints.ccx_status()
    print(status.message)
    if require_ccx and not status.found:
        return 1
    return 0


# python-solvespace publishes wheels for Windows and macOS up to Python 3.11
# and none for Linux. Everywhere else pip compiles it from source.
_SOLVER_WHEEL_PLATFORMS = ("win32", "darwin")
_SOLVER_LAST_WHEEL_PYTHON = (3, 11)


def _solver_needs_compiler(platform: str | None = None, version=None) -> bool:
    """True when pip will build python-solvespace from source on this machine."""

    platform = sys.platform if platform is None else platform
    version = sys.version_info if version is None else version
    if platform in _SOLVER_WHEEL_PLATFORMS:
        return tuple(version[:2]) > _SOLVER_LAST_WHEEL_PYTHON
    return True


def _find_cxx_compiler() -> str | None:
    """The C++ compiler pip would use, or None when none is on PATH."""

    configured = os.environ.get("CXX", "").split()
    names = ([configured[0]] if configured else []) + ["c++", "g++", "clang++"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _python_headers_found() -> bool:
    include = sysconfig.get_paths().get("include")
    return bool(include) and (Path(include) / "Python.h").is_file()


def _glu_found() -> bool:
    return ctypes.util.find_library("GLU") is not None


def _linux_package_hint(apt: str, dnf: str, pacman: str) -> str:
    return "\n".join(
        (
            f"    Debian or Ubuntu: sudo apt-get install -y {apt}",
            f"    Fedora:           sudo dnf install -y {dnf}",
            f"    Arch:             sudo pacman -S {pacman}",
        )
    )


def _missing_prerequisites(*, build: bool = True) -> list[str]:
    """Plain-language notes for system pieces this install needs and lacks.

    ``build`` adds the compiler checks for the pip step. The libGLU check is a
    runtime need: Gmsh, which meshes parts for the stress check, loads it.
    """

    problems: list[str] = []
    if sys.platform.startswith("linux") and not _glu_found():
        problems.append(
            "libGLU was not found. Gmsh needs it to mesh parts for the stress check. "
            "Install it with:\n" + _linux_package_hint("libglu1-mesa", "mesa-libGLU", "glu")
        )
    if not build or not _solver_needs_compiler():
        return problems
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    if sys.platform == "win32":
        problems.append(
            f"The sketch solver (python-solvespace) has no ready-made package for Python {version} "
            "on Windows, so pip compiles it. That needs the Microsoft C++ Build Tools "
            "(https://visualstudio.microsoft.com/visual-cpp-build-tools/, workload "
            '"Desktop development with C++"). Python 3.11 avoids the compile.'
        )
        return problems
    if sys.platform == "darwin":
        if _find_cxx_compiler() is None:
            problems.append(
                f"The sketch solver (python-solvespace) has no ready-made package for Python "
                f"{version} on macOS, so pip compiles it, and no C++ compiler was found. "
                "Install one with `xcode-select --install`, or use Python 3.11."
            )
        return problems
    if _find_cxx_compiler() is None:
        problems.append(
            "No C++ compiler was found. The sketch solver (python-solvespace) has no "
            "ready-made package for Linux, so pip compiles it on every Python version. "
            "Install a compiler with:\n" + _linux_package_hint("g++", "gcc-c++", "gcc")
        )
    if not _python_headers_found():
        problems.append(
            f"The Python {version} headers (Python.h) were not found. pip needs them to "
            "compile the sketch solver. Install them with:\n"
            + _linux_package_hint("python3-dev", "python3-devel", "python")
        )
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
