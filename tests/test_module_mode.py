"""Module mode must not import Python files from the working directory.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, written outside the
project. `python -m aicad` from a project folder must not create it.
`python -P -m aicad` and the aicad command stay usable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_CANARY_TEXT = "CANARY_HIT\n"
_REFUSAL = "Run aicad instead, or run from another folder."


def _outside(tmp_path: Path) -> Path:
    return tmp_path / "SANDBOX_CANARY.txt"


def _canary_source(outside: Path) -> str:
    return (
        "from pathlib import Path\n"
        f"Path({str(outside)!r}).write_text({_CANARY_TEXT!r}, encoding='utf-8')\n"
    )


def _project(tmp_path: Path, *, session: bool = True, aicad_dir: bool = False) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    if session:
        (folder / "session.json").write_text('{"units": "mm", "parts": []}\n', encoding="utf-8")
    if aicad_dir:
        (folder / ".aicad").mkdir()
    return folder


def _env(project: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONSAFEPATH", None)
    if project is not None:
        # The working directory is also present as later path entries.
        env["PYTHONPATH"] = os.pathsep.join([str(project), os.curdir])
    return env


def _run(args: list[str], cwd: Path, project_on_path: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=_env(cwd if project_on_path else None),
        capture_output=True,
        text=True,
        check=False,
    )


def _console_script() -> Path:
    script = Path(sys.executable).with_name("aicad")
    if script.is_file() and os.access(script, os.X_OK):
        return script
    found = shutil.which("aicad")
    if found:
        return Path(found)
    pytest.skip("aicad console script is not installed")


def _assert_canary_absent(completed: subprocess.CompletedProcess[str], outside: Path) -> None:
    assert not outside.exists()
    assert "CANARY_HIT" not in completed.stdout
    assert "CANARY_HIT" not in completed.stderr


def _assert_help(completed: subprocess.CompletedProcess[str], outside: Path) -> None:
    _assert_canary_absent(completed, outside)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("usage: aicad")
    assert "python -m aicad" not in completed.stdout
    assert "python -m aicad" not in completed.stderr


def _assert_refused(completed: subprocess.CompletedProcess[str], outside: Path, label: str) -> None:
    _assert_canary_absent(completed, outside)
    assert completed.returncode != 0
    assert "Traceback" not in completed.stderr
    assert _REFUSAL in completed.stderr
    assert label in completed.stderr
    assert "python -m aicad" not in completed.stderr
    assert not completed.stdout.startswith("usage: aicad")


def test_module_mode_does_not_import_json_from_the_project(tmp_path):
    project = _project(tmp_path)
    outside = _outside(tmp_path)
    (project / "json.py").write_text(_canary_source(outside), encoding="utf-8")
    completed = _run([sys.executable, "-m", "aicad", "--help"], project, project_on_path=True)
    _assert_help(completed, outside)


def test_safe_path_module_mode_stays_usable(tmp_path):
    project = _project(tmp_path)
    outside = _outside(tmp_path)
    (project / "json.py").write_text(_canary_source(outside), encoding="utf-8")
    (project / "stat.py").write_text(_canary_source(outside), encoding="utf-8")
    completed = _run([sys.executable, "-P", "-m", "aicad", "--help"], project)
    _assert_help(completed, outside)


def test_console_script_stays_usable_in_the_project(tmp_path):
    project = _project(tmp_path)
    outside = _outside(tmp_path)
    (project / "json.py").write_text(_canary_source(outside), encoding="utf-8")
    (project / "stat.py").write_text(_canary_source(outside), encoding="utf-8")
    completed = _run([str(_console_script()), "--help"], project)
    _assert_help(completed, outside)


def test_a_project_module_that_was_already_imported_is_refused(tmp_path):
    project = _project(tmp_path)
    outside = _outside(tmp_path)
    (project / "stat.py").write_text(_canary_source(outside), encoding="utf-8")
    completed = _run([sys.executable, "-m", "aicad", "--help"], project, project_on_path=True)
    _assert_refused(completed, outside, "stat.py")


def test_a_project_package_that_was_already_imported_is_refused(tmp_path):
    project = _project(tmp_path, session=False, aicad_dir=True)
    outside = _outside(tmp_path)
    package = project / "stat"
    package.mkdir()
    (package / "__init__.py").write_text(_canary_source(outside), encoding="utf-8")
    completed = _run([sys.executable, "-m", "aicad", "--help"], project)
    _assert_refused(completed, outside, "stat/")


def test_a_part_script_in_the_project_does_not_refuse_module_mode(tmp_path):
    project = _project(tmp_path)
    outside = _outside(tmp_path)
    (project / "bracket.py").write_text(_canary_source(outside), encoding="utf-8")
    completed = _run([sys.executable, "-m", "aicad", "--help"], project, project_on_path=True)
    _assert_help(completed, outside)


def test_a_folder_that_is_not_a_project_does_not_refuse(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    outside = _outside(tmp_path)
    (folder / "json.py").write_text(_canary_source(outside), encoding="utf-8")
    (folder / "stat.py").write_text(_canary_source(outside), encoding="utf-8")
    completed = _run([sys.executable, "-m", "aicad", "--help"], folder, project_on_path=True)
    _assert_help(completed, outside)


def test_importing_the_library_from_a_project_does_not_refuse(tmp_path):
    project = _project(tmp_path)
    outside = _outside(tmp_path)
    (project / "stat.py").write_text(_canary_source(outside), encoding="utf-8")
    completed = _run([sys.executable, "-c", "import aicad; print('imported')"], project)
    _assert_canary_absent(completed, outside)
    assert completed.returncode == 0, completed.stderr
    assert "imported" in completed.stdout


def test_module_mode_of_the_cli_module_does_not_import_json(tmp_path):
    project = _project(tmp_path)
    outside = _outside(tmp_path)
    (project / "json.py").write_text(_canary_source(outside), encoding="utf-8")
    completed = _run([sys.executable, "-m", "aicad.cli", "--help"], project, project_on_path=True)
    _assert_help(completed, outside)


def _shadow_package(project: Path, outside: Path) -> None:
    package = project / "aicad"
    package.mkdir()
    (package / "__init__.py").write_text(_canary_source(outside), encoding="utf-8")
    (package / "cli.py").write_text(_canary_source(outside), encoding="utf-8")


def test_a_project_package_named_aicad_is_not_imported(tmp_path):
    project = _project(tmp_path)
    outside = _outside(tmp_path)
    _shadow_package(project, outside)
    completed = _run(
        [sys.executable, "-c", "import aicad; print(aicad.__file__)"],
        project,
        project_on_path=True,
    )
    _assert_canary_absent(completed, outside)
    assert completed.returncode == 0, completed.stderr
    loaded = Path(completed.stdout.strip())
    assert loaded.is_file()
    assert loaded != (project / "aicad" / "__init__.py").resolve()
    completed = _run([sys.executable, "-m", "aicad", "--help"], project, project_on_path=True)
    _assert_help(completed, outside)
    completed = _run(
        [sys.executable, "-c", "import aicad.cli; print(aicad.cli.__file__)"],
        project,
    )
    _assert_canary_absent(completed, outside)
    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()) != (project / "aicad" / "cli.py").resolve()
    completed = _run([str(_console_script()), "--help"], project)
    _assert_help(completed, outside)
