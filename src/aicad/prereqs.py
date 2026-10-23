"""External programs aicad can use, and how to install them.

CalculiX (``ccx``) is GPL-2.0-only. aicad does not link it. It only starts
``ccx`` as a separate program, and only when a strength or vibration check asks
for it.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass


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
    conda = "If you use conda, this works on Windows, Mac, and Linux:\n    conda install conda-forge::calculix\n"
    if system == "win32":
        return (
            shared
            + "On Windows, either use conda or a Windows build of CalculiX.\n"
            + conda
            + "Without conda, download a release from\n"
            + "    https://github.com/calculix/CalculiX-Windows/releases\n"
            + "and add the folder that contains ccx.exe to your PATH. Open a new terminal and run `ccx`."
        )
    if system == "darwin":
        return (
            shared
            + "On a Mac, Homebrew can install it:\n"
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


def ccx_status() -> SolverStatus:
    """Look for the CalculiX solver on PATH."""

    found = shutil.which("ccx")
    if found:
        return SolverStatus(True, found, f"CalculiX is available ({found}).")
    return SolverStatus(False, None, ccx_hint())
