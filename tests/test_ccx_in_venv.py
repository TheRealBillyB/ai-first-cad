"""CalculiX installed into ``.venv`` by ``install.py --with-calculix`` runs from the app.

aicad does not start a program inside an open project. A hand test opened the
install folder itself as the project (``aicad app --workspace .``), so the
solver in ``.venv\\calculix`` was skipped and the Strength check said CalculiX
was not on PATH. A folder that holds the running install is now refused as a
project, with a message that says to pick a project folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aicad.app_worker import GeometryWorker
from aicad.document import Document
from aicad.errors import DocumentError, GeometryError
from aicad.fea import fea_available
from aicad.paths import refuse_install_as_project
from aicad.prereqs import find_program
from aicad.workspace import Workspace

_AVAILABLE, _REASON = fea_available()
needs_solver = pytest.mark.skipif(not _AVAILABLE, reason=_REASON)

# The worker the app starts, with sys.prefix moved to a stand-in install.
_WORKER_WITH_PREFIX = """
import sys
sys.prefix = sys.argv[1]
from aicad.app_worker import _main
raise SystemExit(_main())
"""

_BEAM = [{"op": "add", "shape": "box", "size": [20, 4, 4]}]
_SETUP = {
    "material": "steel",
    "mesh_size_mm": 3.0,
    "target_safety_factor": 1.0,
    "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
    "loads": [{"name": "tip", "type": "force", "vector": [0, 0, -100], "select": {"kind": "plane", "normal": [1, 0, 0]}}],
}


def _ccx_names() -> tuple[str, ...]:
    return ("ccx.exe", "ccx.bat", "ccx.cmd", "ccx") if os.name == "nt" else ("ccx",)


def _path_without_ccx() -> str:
    kept = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and not any((Path(entry) / name).exists() for name in _ccx_names()):
            kept.append(entry)
    return os.pathsep.join(kept)


def _link_directory(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink_directory(link: Path) -> None:
    """Remove only the link, never the solver it points at."""
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


@needs_solver
def test_the_app_solves_with_ccx_only_in_the_installs_calculix_folder(tmp_path, monkeypatch):
    """The real app worker and a real CalculiX run, with ccx nowhere on PATH.

    The stand-in install has the layout ``install.py --with-calculix`` makes:
    ``<venv>\\calculix\\prefix\\solver-bin``. That folder links to the real solver.
    """
    solver = Path(find_program("ccx")).resolve()
    venv = tmp_path / "ai-first-cad" / ".venv"
    solver_bin = venv / "calculix" / "prefix" / "solver-bin"
    _link_directory(solver_bin, solver.parent)
    try:
        path = _path_without_ccx()
        monkeypatch.setenv("PATH", path)
        import shutil

        assert shutil.which("ccx", path=path) is None, "ccx is still on PATH, so this would not test the install folder"
        monkeypatch.setattr(
            "aicad.app_worker._worker_command",
            lambda: [sys.executable, "-P", "-c", _WORKER_WITH_PREFIX, str(venv)],
        )
        running = GeometryWorker(tmp_path / "ai-first-cad" / "aicad_workspace")
        try:
            running.execute("create_part_from_ops", {"name": "beam", "ops": _BEAM}, "ui")
            result = running.execute("solve_mechanics", {"name": "beam", "setup": _SETUP}, "ui")
        finally:
            running.close()
        assert result["status"] == "solved", result
        assert not any("not on" in warning and "PATH" in warning for warning in result.get("warnings", []))
    finally:
        _unlink_directory(solver_bin)
    assert solver.is_file(), "removing the link must not touch the real solver"


def test_the_folder_that_holds_the_install_is_not_opened_as_a_project(tmp_path, monkeypatch):
    install = tmp_path / "ai-first-cad"
    (install / ".venv").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(install / ".venv"))
    for folder in (install, install / ".venv", tmp_path):
        with pytest.raises(DocumentError) as refused:
            Document(folder)
        message = str(refused.value)
        assert "holds the aicad install" in message
        assert "Pick or create a project folder" in message
        assert str(folder.resolve() / "aicad_workspace") in message
    assert not (install / "session.json").exists()
    assert not (tmp_path / "session.json").exists()


def test_a_project_folder_beside_the_install_still_opens(tmp_path, monkeypatch):
    install = tmp_path / "ai-first-cad"
    (install / ".venv").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(install / ".venv"))
    document = Document(install / "aicad_workspace")
    assert document.workspace == (install / "aicad_workspace").resolve()


def test_the_running_install_itself_is_refused():
    """No stand-in: the Python and the aicad code running this test."""
    with pytest.raises(DocumentError, match="holds the aicad install"):
        refuse_install_as_project(Path(sys.prefix).parent, DocumentError)
    with pytest.raises(DocumentError, match="holds the aicad install"):
        refuse_install_as_project(Path(__import__("aicad").__file__).parent, DocumentError)


def test_the_app_says_why_it_will_not_open_the_install_folder(tmp_path, monkeypatch):
    """``aicad app --workspace .`` from the install folder."""
    install = tmp_path / "ai-first-cad"
    venv = install / ".venv"
    venv.mkdir(parents=True)
    monkeypatch.setattr(
        "aicad.app_worker._worker_command",
        lambda: [sys.executable, "-P", "-c", _WORKER_WITH_PREFIX, str(venv)],
    )
    with pytest.raises(Exception, match="holds the aicad install") as refused:
        GeometryWorker(install)
    assert "Pick or create a project folder" in str(refused.value)
    assert not (install / "session.json").exists()


def test_save_as_into_the_install_folder_is_refused(tmp_path, monkeypatch):
    install = tmp_path / "ai-first-cad"
    (install / ".venv").mkdir(parents=True)
    workspace = Workspace(tmp_path / "project")
    monkeypatch.setattr(sys, "prefix", str(install / ".venv"))
    with pytest.raises(GeometryError, match="holds the aicad install"):
        workspace.execute("save_project_as", {"path": str(install)}, "ui")
    assert not (install / "session.json").exists()
    assert workspace.root == (tmp_path / "project").resolve()
