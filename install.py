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
import importlib.util
import os
import shutil
import subprocess
import sys
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
    if not args.no_system:
        _install_linux_packages()
    python = _ensure_venv()
    _pip_install(python)
    print()
    print(_ccx_message(hints))
    print()
    print(_ready_message(python))
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


def _pip_env() -> dict[str, str]:
    """Environment for the pip install that compiles python-solvespace.

    That package ships a C11 mimalloc. It initializes an atomic with
    ATOMIC_VAR_INIT, which C23 removed. Current GCC defaults to C23, so the
    C files are compiled as C11 unless the caller already chose a standard.
    """
    env = os.environ.copy()
    if sys.platform == "win32":
        return env
    flags = env.get("CFLAGS", "")
    if "-std=" not in flags:
        env["CFLAGS"] = (flags + " -std=gnu11").strip()
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


def _install_linux_packages() -> None:
    if not sys.platform.startswith("linux"):
        return
    if shutil.which("apt-get") is None:
        print("This Linux does not use apt. Install CalculiX yourself if the check below asks for it.")
        return
    if not _passwordless_sudo():
        print("Skipping system packages because sudo needs a password. The command is:")
        print("    sudo apt-get update && sudo apt-get install -y " + " ".join(APT_PACKAGES))
        return
    print("Installing system libraries and CalculiX (sudo apt-get).")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    subprocess.run(
        ["sudo", "-n", "apt-get", "update"],
        cwd=ROOT,
        check=True,
        env=env,
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    subprocess.run(
        ["sudo", "-n", "apt-get", "install", "-y", "--no-install-recommends", *APT_PACKAGES],
        cwd=ROOT,
        check=True,
        env=env,
        timeout=_INSTALL_TIMEOUT_SECONDS,
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
    status = hints.ccx_status()
    print(status.message)
    if require_ccx and not status.found:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
