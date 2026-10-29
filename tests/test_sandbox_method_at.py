"""Geometry libraries do not read a config from the account home.

The marker is CANARY_HIT. ezdxf skips that fixed path. A design script gets
an empty home directory, and plugin consent stays on the account home.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aicad.paths import prepare_geometry_import
from aicad.sandbox import allow_design_scripts, run_design_script, sandbox_supported

_MARKER = "CANARY_HIT"


def test_a_home_ezdxf_config_is_not_loaded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ini = home / ".config" / "ezdxf" / "ezdxf.ini"
    ini.parent.mkdir(parents=True)
    ini.write_text(f"[core]\nSUPPORT_DIRS = {_MARKER}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    prepare_geometry_import()
    import ezdxf._options as options

    files = [Path(path).expanduser().resolve() for path in options.config_files()]
    assert ini.resolve() not in files
    fresh = options.Options()
    assert _MARKER not in fresh.get("core", "SUPPORT_DIRS")
    assert all(path.resolve() != ini.resolve() for path in fresh.loaded_config_files)
    from aicad.paths import packaged_fontconfig_file

    fontconfig = Path(os.environ["FONTCONFIG_FILE"])
    assert fontconfig == packaged_fontconfig_file()
    assert home.resolve() not in fontconfig.resolve().parents
    assert _MARKER not in fontconfig.read_text(encoding="utf-8")
    config = Path(os.environ["MPLCONFIGDIR"])
    assert config.is_dir()
    assert home.resolve() not in config.resolve().parents
    assert config.resolve() != home.resolve()


def test_a_design_script_gets_an_empty_library_home(tmp_path, monkeypatch):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    home = tmp_path / "account-home"
    ini = home / ".config" / "ezdxf" / "ezdxf.ini"
    ini.parent.mkdir(parents=True)
    ini.write_text(f"[core]\nSUPPORT_DIRS = {_MARKER}\n", encoding="utf-8")
    (home / ".gmshrc").write_text("General.Terminal = 5;\n", encoding="utf-8")
    (home / ".fonts.conf").write_text("<fontconfig/>\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    planted = project / ".aicad" / "library-config"
    (planted / ".config" / "ezdxf").mkdir(parents=True)
    (planted / ".gmshrc").write_text(f"{_MARKER}\n", encoding="utf-8")
    (planted / ".fonts.conf").write_text("<fontconfig/>\n", encoding="utf-8")
    (planted / ".config" / "ezdxf" / "ezdxf.ini").write_text(
        f"[core]\nSUPPORT_DIRS = {_MARKER}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    allow_design_scripts(project)
    payload = run_design_script(
        project,
        "import pathlib\n"
        "import aicad.plugins as plugins\n"
        "home = pathlib.Path.home()\n"
        "print('home', home)\n"
        "print('gmshrc', (home / '.gmshrc').is_file())\n"
        "print('fonts', (home / '.fonts.conf').is_file())\n"
        "print('ini', (home / '.config' / 'ezdxf' / 'ezdxf.ini').is_file())\n"
        "print('consent', plugins.consent_file())\n",
    )
    assert payload["ok"] is True, payload.get("error")
    text = payload["stdout"]
    assert "gmshrc False" in text
    assert "fonts False" in text
    assert "ini False" in text
    assert _MARKER not in text
    reported = text.split("home ", 1)[1].splitlines()[0]
    assert str(home) not in reported
    assert reported.replace("\\", "/").endswith(".aicad/library-config")
    library = project / ".aicad" / "library-config"
    consent = text.split("consent ", 1)[1].splitlines()[0]
    assert Path(consent) == home / ".config" / "aicad" / "plugins.json"
    assert library.resolve() not in Path(consent).resolve().parents
