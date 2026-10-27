#!/usr/bin/env python3
"""Compile python-solvespace from source with the installer's compiler flags.

The test suite installs a wheel when one is published, so those jobs never
compile the solver. This command refuses a wheel, ignores the pip cache, and
checks the build log. When the installer selected a C standard, a C file has
to be compiled with that standard. When it left CFLAGS unset, which it does
for MSVC, a C file still has to be compiled. A C++ file must not pick up a
later ``-std=gnu11``.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOLVER_REQUIREMENT = "python-solvespace==3.0.8"
_C_UNIT = re.compile(r"\.c(?![\w+])", re.IGNORECASE)
_CPP_UNIT = re.compile(r"\.cpp(?![\w])", re.IGNORECASE)
_STANDARD = re.compile(r"-std=[^\s]+")


def load_install():
    path = ROOT / "install.py"
    spec = importlib.util.spec_from_file_location("aicad_install", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chosen_standard(cflags: str | None) -> str | None:
    """The last ``-std=`` token in CFLAGS, or None when CFLAGS is unset.

    An empty string means CFLAGS was set and did not name a standard.
    """

    if cflags is None:
        return None
    found = _STANDARD.findall(cflags)
    if not found:
        return ""
    return found[-1]


def solver_wheel_used(log: str) -> bool:
    """True when pip downloaded a python-solvespace wheel instead of building one.

    A source build still writes a wheel at the end. That filename is not a
    downloaded wheel.
    """

    for line in log.splitlines():
        lowered = line.lower()
        if ".whl" not in lowered:
            continue
        if "python-solvespace" not in lowered and "python_solvespace" not in lowered:
            continue
        if "downloading " in lowered or "using cached" in lowered or ".whl.metadata" in lowered:
            return True
    return False


def built_from_source(log: str) -> bool:
    return "building wheel for python-solvespace" in log.lower()


def c_files_use(log: str, standard: str) -> bool:
    if not standard:
        return False
    return any(standard in line and _C_UNIT.search(line) for line in log.splitlines())


def c_file_compiled(log: str) -> bool:
    return any(_C_UNIT.search(line) for line in log.splitlines())


def cxx_standard_stays(log: str) -> bool:
    """True when ``-std=gnu11`` is not the last standard on a C++ compile."""

    for line in log.splitlines():
        if _CPP_UNIT.search(line) is None:
            continue
        gnu = line.rfind("-std=gnu11")
        cxx = max(line.rfind("-std=c++17"), line.lower().rfind("/std:c++17"))
        if gnu != -1 and gnu > cxx:
            return False
    return True


def report_problems(log: str, cflags: str | None) -> list[str]:
    problems: list[str] = []
    if solver_wheel_used(log):
        problems.append("python-solvespace was installed from a wheel. The source build did not run.")
    if not built_from_source(log):
        problems.append("The log does not show a source build of python-solvespace.")
    standard = chosen_standard(cflags)
    if standard is None:
        if not c_file_compiled(log):
            problems.append("The solver's C files were not compiled.")
    elif not standard:
        problems.append("CFLAGS did not name a C standard.")
    elif not c_files_use(log, standard):
        problems.append(f"The solver's C files were not compiled with {standard}.")
    if not cxx_standard_stays(log):
        problems.append("A C++ file was compiled with -std=gnu11 after its C++ standard.")
    return problems


def _stream(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        chunks.append(line)
    return proc.wait(), "".join(chunks)


def main() -> int:
    module = load_install()
    env = module._pip_env()
    env["PIP_NO_CACHE_DIR"] = "1"
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--no-binary=python-solvespace",
        "-v",
        SOLVER_REQUIREMENT,
    ]
    code, log = _stream(command, env)
    problems = report_problems(log, env.get("CFLAGS"))
    if code != 0:
        return code
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    imported = subprocess.run(
        [sys.executable, "-I", "-c", "import python_solvespace"],
        check=False,
    )
    return imported.returncode


if __name__ == "__main__":
    raise SystemExit(main())
