"""`install.py --with-calculix` is opt-in, and aicad finds what it installed."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from aicad.prereqs import find_program

ROOT = Path(__file__).resolve().parents[1]


def _load_install():
    spec = importlib.util.spec_from_file_location("aicad_install_with_calculix", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _quiet_install(monkeypatch, install, tmp_path, calls, returncode=0):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    monkeypatch.setattr(install, "_venv_python", lambda: python)
    monkeypatch.setattr(install, "_ensure_venv", lambda: python)
    monkeypatch.setattr(install, "_pip_install", lambda _python: None)
    monkeypatch.setattr(install, "_install_linux_packages", lambda: True)
    monkeypatch.setattr(install, "_missing_prerequisites", lambda build=True: [])
    monkeypatch.setattr(install.sys, "platform", "win32")

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(install.subprocess, "run", run)


def test_nothing_is_downloaded_unless_asked(monkeypatch, tmp_path):
    install = _load_install()
    calls = []
    _quiet_install(monkeypatch, install, tmp_path, calls)
    assert install.main(["--no-system"]) == 0
    assert not [command for command in calls if "install_calculix.py" in " ".join(map(str, command))]


def test_the_flag_runs_the_pinned_script_into_the_virtual_environment(monkeypatch, tmp_path):
    install = _load_install()
    calls = []
    _quiet_install(monkeypatch, install, tmp_path, calls)
    assert install.main(["--no-system", "--with-calculix"]) == 0
    command = next(command for command in calls if "install_calculix.py" in " ".join(map(str, command)))
    assert str(tmp_path / ".venv" / "calculix" / "prefix") in command
    assert str(tmp_path / ".venv" / "calculix" / "work") in command


def test_a_failed_download_is_a_failed_install(monkeypatch, tmp_path):
    install = _load_install()
    calls = []
    _quiet_install(monkeypatch, install, tmp_path, calls, returncode=1)
    assert install.main(["--no-system", "--with-calculix"]) == 1


def _installed_solver(prefix: Path) -> Path:
    folder = prefix / "calculix" / "prefix" / "solver-bin"
    folder.mkdir(parents=True)
    solver = folder / ("ccx.exe" if os.name == "nt" else "ccx")
    solver.write_bytes(b"")
    solver.chmod(0o755)
    return solver


def test_aicad_finds_the_installed_solver_with_nothing_on_path(monkeypatch, tmp_path):
    solver = _installed_solver(tmp_path)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setenv("PATH", "")
    assert find_program("ccx") == str(solver.resolve())


def test_a_solver_on_path_comes_first(monkeypatch, tmp_path):
    _installed_solver(tmp_path / "venv")
    first = tmp_path / "first"
    first.mkdir()
    solver = first / ("ccx.exe" if os.name == "nt" else "ccx")
    solver.write_bytes(b"")
    solver.chmod(0o755)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setenv("PATH", str(first))
    assert find_program("ccx") == str(solver.resolve())


def test_an_installed_solver_inside_the_project_is_not_used(monkeypatch, tmp_path):
    _installed_solver(tmp_path)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setenv("PATH", "")
    assert find_program("ccx", project=tmp_path) is None
