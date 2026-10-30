"""Library config is not rewritten or redirected by project content.

The marker is CANARY_HIT. A project matplotlibrc is not the model process
config. A script cannot point HOME at another directory or write the library
config directory. The marker stays where it was written.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_MARKER = "CANARY_HIT"


def test_a_project_matplotlibrc_is_not_used(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    rc = tmp_path / "matplotlibrc"
    rc.write_text("savefig.dpi: 13\n", encoding="utf-8")
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("matplotlib", None)
    from aicad.paths import prepare_geometry_import

    prepare_geometry_import()
    import matplotlib

    assert matplotlib.rcParams["savefig.dpi"] != 13
    assert str(matplotlib.rcParams["savefig.dpi"]) != "13.0"
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"


def test_library_config_paths_cannot_be_redirected():
    import os as os_module

    from aicad.sandbox import lock_library_environment

    original = os_module.environ
    putenv = os_module.putenv
    home = original.get("HOME")
    try:
        lock_library_environment()
        with pytest.raises(PermissionError):
            os_module.environ["HOME"] = str(Path("/tmp") / "canary-home")
        with pytest.raises(PermissionError):
            os_module.environ["MPLCONFIGDIR"] = str(Path("/tmp") / "canary-mpl")
        assert os_module.environ.get("HOME") == home
        assert "canary-home" not in str(os_module.environ.get("HOME", ""))
    finally:
        os_module.environ = original
        os_module.putenv = putenv  # type: ignore[assignment]


def test_a_design_script_cannot_write_or_redirect_library_config(tmp_path, monkeypatch):
    from aicad.sandbox import allow_design_scripts, run_design_script, sandbox_supported

    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    allow_design_scripts(project)
    payload = run_design_script(
        project,
        "import pathlib\n"
        "import aicad.sandbox as sandbox\n"
        "home = pathlib.Path.home()\n"
        "try:\n"
        "    (home / '.gmshrc').write_text('CANARY_HIT\\n', encoding='utf-8')\n"
        "    print('wrote')\n"
        "except Exception as exc:\n"
        "    print('write', type(exc).__name__)\n"
        "try:\n"
        "    sandbox.os.environ['HOME'] = str(pathlib.Path('redirected').resolve())\n"
        "    print('redirected')\n"
        "except Exception as exc:\n"
        "    print('redirect', type(exc).__name__)\n"
        "print('home', pathlib.Path.home())\n"
        "print('gmshrc', (home / '.gmshrc').is_file())\n",
    )
    assert payload.get("ok") is True, payload.get("error")
    text = payload.get("stdout") or ""
    assert "wrote" not in text.split()
    assert "gmshrc False" in text
    assert "redirected" not in text.split()
    assert "library-config" in text
    assert _MARKER not in text
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
