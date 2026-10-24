"""The macOS and Windows CI CalculiX install is pinned and checksum-checked.

These tests do not download the solver. They check the lock and the decisions
the installer makes before it fetches anything.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _installer():
    path = ROOT / ".github" / "scripts" / "install_calculix.py"
    spec = importlib.util.spec_from_file_location("install_calculix", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["install_calculix"] = module
    spec.loader.exec_module(module)
    return module


def _lock() -> dict:
    return json.loads((ROOT / ".github" / "calculix.lock.json").read_text(encoding="utf-8"))


def test_lock_pins_calculix_and_openblas():
    module = _installer()
    lock = _lock()
    module.validate_lock(lock)
    osx = lock["platforms"]["osx-arm64"]["packages"]
    windows = lock["platforms"]["win-64"]["packages"]
    assert [pkg["build"] for pkg in osx if pkg["name"] == "calculix"] == ["pl5321hd17ca8b_8"]
    assert [pkg["build"] for pkg in windows if pkg["name"] == "calculix"] == ["pl5321h7abefab_8"]
    assert lock["platforms"]["osx-arm64"]["path"] == ["bin"]
    assert lock["platforms"]["win-64"]["path"] == ["Library/bin", "Library/mingw-w64/bin"]
    assert len(osx) == 11
    assert len(windows) == 16


def test_a_lock_that_pulls_mkl_is_refused():
    module = _installer()
    lock = _lock()
    lock["platforms"]["win-64"]["packages"][0]["filename"] = "mkl-2024.1-h123.conda"
    with pytest.raises(ValueError, match="MKL"):
        module.validate_lock(lock)


def test_linux_does_not_download_calculix(capsys):
    module = _installer()
    assert module.install({}, system="linux", machine="x86_64", work=Path("/tmp"), prefix=Path("/tmp")) == 0
    assert "install.py" in capsys.readouterr().out


def test_an_unknown_runner_stops_instead_of_skipping():
    module = _installer()
    with pytest.raises(module.UnsupportedRunner, match="osx-arm64 and win-64"):
        module.select_platform("darwin", "x86_64")
    with pytest.raises(module.UnsupportedRunner):
        module.select_platform("win32", "ARM64")
    assert module.select_platform("darwin", "arm64") == "osx-arm64"
    assert module.select_platform("win32", "AMD64") == "win-64"
    assert module.select_platform("linux", "aarch64") is None


def test_the_ccx_directory_is_searched_first():
    module = _installer()
    directories = [Path("/solver/Library/bin"), Path("/solver/Library/mingw-w64/bin")]
    lines = module.github_path_lines(directories)
    path: list[str] = []
    for line in lines:
        path.insert(0, line)
    assert path[0] == "/solver/Library/bin"
    assert path[1] == "/solver/Library/mingw-w64/bin"


def test_the_link_uses_one_package_cache_that_the_job_creates():
    module = _installer()
    env = module.link_environment(
        {"CONDA_PKGS_DIRS": "/missing/runner/pkgs", "PATH": "/usr/bin"},
        Path("/work/root/pkgs"),
    )
    assert env["CONDA_PKGS_DIRS"] == "/work/root/pkgs"
    assert env["PATH"] == "/usr/bin"


def test_a_dll_that_is_not_beside_ccx_exe_is_copied_there(tmp_path):
    module = _installer()
    exe_dir = tmp_path / "Library" / "bin"
    other = tmp_path / "Library" / "mingw-w64" / "bin"
    exe_dir.mkdir(parents=True)
    other.mkdir(parents=True)
    exe = exe_dir / "ccx.exe"
    exe.write_bytes(b"exe")
    (exe_dir / "libblas.dll").write_bytes(b"blas")
    (other / "libarpack.dll").write_bytes(b"arpack")
    (other / "libblas.dll").write_bytes(b"other-blas")
    copied = module.stage_sibling_dlls(exe, [exe_dir, other])
    assert [path.name for path in copied] == ["libarpack.dll"]
    assert (exe_dir / "libarpack.dll").read_bytes() == b"arpack"
    assert (exe_dir / "libblas.dll").read_bytes() == b"blas"
    posix = tmp_path / "bin" / "ccx"
    posix.parent.mkdir()
    posix.write_bytes(b"ccx")
    assert module.stage_sibling_dlls(posix, [other]) == []


def test_a_missing_library_is_not_treated_as_a_started_solver():
    module = _installer()
    assert module.solver_started(0, "Usage: CalculiX.exe -i jobname\n", "")
    assert module.solver_started(201, "", "This is Version 2.23\nCalculiX\n")
    assert not module.solver_started(3221225781, "", "The code execution cannot proceed because libarpack.dll was not found.")
    assert not module.solver_started(-9, "CalculiX", "")
