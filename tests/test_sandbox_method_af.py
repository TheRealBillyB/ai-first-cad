"""Geometry libraries do not read a project config or a parent variable.

The marker is CANARY_HIT. A project ``.gmshrc`` and a parent font or
OpenCascade variable are not applied.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aicad import fea, kernel
from aicad.paths import prepare_geometry_import
from aicad.sandbox import program_environment, scrubbed_environment

_MARKER = "CANARY_HIT"


_LIBRARY_VARS = (
    "FONTCONFIG_FILE",
    "FONTCONFIG_PATH",
    "CSF_OCCTResourcePath",
    "CSF_PluginDefaults",
    "GMSH_HOME",
    "CASROOT",
    "FREETYPE_PROPERTIES",
    "MMGT_OPT",
    "EZDXF_CONFIG_FILE",
)


def test_parent_library_variables_are_removed(tmp_path):
    marker = tmp_path / "canary.conf"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    saved = {name: os.environ.get(name) for name in _LIBRARY_VARS}
    try:
        for name in _LIBRARY_VARS:
            os.environ[name] = str(marker)
        prepare_geometry_import()
        for name in _LIBRARY_VARS:
            if name == "EZDXF_CONFIG_FILE":
                continue
            assert name not in os.environ
        assert os.environ["EZDXF_CONFIG_FILE"] != str(marker)
        assert _MARKER not in os.environ["EZDXF_CONFIG_FILE"]
        assert Path(os.environ["EZDXF_CONFIG_FILE"]).is_file()
        for env in (scrubbed_environment(None), program_environment()):
            upper = {key.upper() for key in env}
            assert "FONTCONFIG_FILE" not in upper
            assert "CSF_OCCTRESOURCEPATH" not in upper
            assert "GMSH_HOME" not in upper
            assert str(marker) not in env.values()
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        prepare_geometry_import()


def test_meshing_does_not_read_gmsh_config_files(monkeypatch):
    import gmsh

    seen: dict = {}

    def stop(*_args, **kwargs):
        seen["kwargs"] = kwargs
        raise RuntimeError("stop")

    monkeypatch.setattr(gmsh, "initialize", stop)
    shape = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10]}])
    with pytest.raises(RuntimeError, match="stop"):
        fea._mesh_attempt(shape, 2.0, 1, algorithm=1, optimize=0, occ_fix=False)
    assert seen["kwargs"].get("readConfigFiles") is False


def test_a_project_gmshrc_does_not_change_an_option(tmp_path, monkeypatch):
    import gmsh

    rc = tmp_path / ".gmshrc"
    rc.write_text("General.Terminal = 5;\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    try:
        gmsh.finalize()
    except Exception:
        pass
    prepare_geometry_import()
    gmsh.initialize(readConfigFiles=False)
    try:
        assert gmsh.option.getNumber("General.Terminal") != 5
    finally:
        gmsh.finalize()
