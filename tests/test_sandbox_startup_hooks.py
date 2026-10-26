"""Startup and install files in a shared project must not run outside it.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT. Opening the project
does not run it. Starting the sandbox interpreter in that folder does not run
it either, including when the import path would otherwise reach into the project.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.kernel import measure
from aicad.sandbox import _child_env, _pythonpath_for_child, run_part_script, sandbox_supported
from aicad.workspace import Workspace

_BOX = "part = Box(1, 1, 1)\n"
_CANARY_TEXT = "CANARY_HIT\n"
_STARTUP_DIRECTORY_NAMES = ("sitecustomize", "usercustomize", "apport_python_hook")


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


def _outside(tmp_path: Path) -> Path:
    return tmp_path / "outside" / "SANDBOX_CANARY.txt"


def _canary_source(outside: Path) -> str:
    return (
        "from pathlib import Path\n"
        f"Path({str(outside)!r}).write_text({_CANARY_TEXT!r}, encoding='utf-8')\n"
    )


def _pth_line(outside: Path) -> str:
    return (
        "import pathlib; pathlib.Path("
        + repr(str(outside))
        + ").write_text('CANARY_HIT\\n', encoding='utf-8')\n"
    )


def _assert_canary_absent(outside: Path) -> None:
    assert not outside.exists()


def _run_box(project: Path):
    _sandbox_or_skip()
    shape = run_part_script(_BOX, project)
    assert measure(shape).volume_mm3 == pytest.approx(1)


def test_a_pth_file_in_the_project_does_not_run_when_python_starts(tmp_path, config_home):
    project = tmp_path / "shared"
    project.mkdir()
    outside = _outside(tmp_path)
    (project / "canary.pth").write_text(_pth_line(outside), encoding="utf-8")
    (project / "hooks").mkdir()
    (project / "hooks" / "canary.pth").write_text(_pth_line(outside), encoding="utf-8")
    _run_box(project)
    _assert_canary_absent(outside)


def test_sitecustomize_in_the_project_does_not_run_when_python_starts(tmp_path, config_home):
    project = tmp_path / "shared"
    project.mkdir()
    outside = _outside(tmp_path)
    (project / "sitecustomize.py").write_text(_canary_source(outside), encoding="utf-8")
    _run_box(project)
    _assert_canary_absent(outside)


def test_usercustomize_in_the_project_does_not_run_when_python_starts(tmp_path, config_home):
    project = tmp_path / "shared"
    project.mkdir()
    outside = _outside(tmp_path)
    (project / "usercustomize.py").write_text(_canary_source(outside), encoding="utf-8")
    _run_box(project)
    _assert_canary_absent(outside)


def test_an_early_import_in_the_project_does_not_run_when_python_starts(tmp_path, config_home):
    """A module the interpreter imports while starting, left in the project."""
    project = tmp_path / "shared"
    project.mkdir()
    outside = _outside(tmp_path)
    (project / "apport_python_hook.py").write_text(_canary_source(outside), encoding="utf-8")
    (project / "os.py").write_text(_canary_source(outside), encoding="utf-8")
    _run_box(project)
    _assert_canary_absent(outside)


def test_a_relative_import_path_is_not_read_from_the_project(tmp_path, monkeypatch):
    project = tmp_path / "shared"
    hooks = project / "vendor_hooks"
    hooks.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", "vendor_hooks")
    env = _child_env(project, project / "job", False)
    pythonpath = env.get("PYTHONPATH", "").split(os.pathsep)
    assert str((tmp_path / "vendor_hooks").resolve()) not in pythonpath
    monkeypatch.chdir(project)
    monkeypatch.setenv("PYTHONPATH", "vendor_hooks")
    env = _child_env(project, project / "job", False)
    pythonpath = env.get("PYTHONPATH", "").split(os.pathsep)
    assert str((project / "vendor_hooks").resolve()) not in pythonpath
    assert str(project.resolve()) not in pythonpath


def test_a_relative_import_path_cannot_run_code_from_the_project(tmp_path, config_home, monkeypatch):
    project = tmp_path / "shared"
    hooks = project / "vendor_hooks"
    hooks.mkdir(parents=True)
    outside = _outside(tmp_path)
    (hooks / "sitecustomize.py").write_text(_canary_source(outside), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", "vendor_hooks")
    _run_box(project)
    _assert_canary_absent(outside)


def test_an_import_path_inside_the_project_cannot_run_startup_code(tmp_path, config_home, monkeypatch):
    project = tmp_path / "shared"
    vendor = project / "vendor"
    package = vendor / "aicad"
    package.mkdir(parents=True)
    outside = _outside(tmp_path)
    (vendor / "os.py").write_text(_canary_source(outside), encoding="utf-8")
    (vendor / "sitecustomize.py").write_text(_canary_source(outside), encoding="utf-8")
    (package / "__init__.py").write_text(_canary_source(outside), encoding="utf-8")
    (package / "sandbox_run.py").write_text(
        _canary_source(outside) + "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(vendor))
    _run_box(project)
    _assert_canary_absent(outside)
    assert _pythonpath_for_child(project, str(vendor)) is None


@pytest.mark.parametrize("name", _STARTUP_DIRECTORY_NAMES)
def test_a_startup_module_directory_is_not_imported_from_its_parent(tmp_path, config_home, monkeypatch, name):
    project = tmp_path / name
    project.mkdir()
    outside = _outside(tmp_path)
    (project / "__init__.py").write_text(_canary_source(outside), encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    assert _pythonpath_for_child(project, str(tmp_path)) is None
    _run_box(project)
    _assert_canary_absent(outside)


def test_opening_a_project_does_not_run_install_metadata(tmp_path, config_home):
    project = tmp_path / "shared"
    project.mkdir()
    outside = _outside(tmp_path)
    source = _canary_source(outside)
    (project / "canary_backend.py").write_text(source, encoding="utf-8")
    (project / "setup.py").write_text(source, encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[build-system]\n"
        "requires = []\n"
        'build-backend = "canary_backend"\n'
        'backend-path = ["."]\n',
        encoding="utf-8",
    )
    (project / "requirements.txt").write_text("-e .\n", encoding="utf-8")
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "pending",
                "source": "script",
                "script": source + _BOX,
            }
        ],
    }
    (project / "session.json").write_text(json.dumps(session), encoding="utf-8")

    document = Document(project)
    assert document.list_parts()[0].built is False
    _assert_canary_absent(outside)

    workspace = Workspace(tmp_path / "here")
    opened = workspace.execute("open_project", {"path": str(project)})
    assert opened["parts"] == ["pending"]
    assert workspace.document.list_parts()[0].built is False
    _assert_canary_absent(outside)

    supported, _reason = sandbox_supported()
    if supported:
        shape = run_part_script(_BOX, project)
        assert measure(shape).volume_mm3 == pytest.approx(1)
    _assert_canary_absent(outside)
