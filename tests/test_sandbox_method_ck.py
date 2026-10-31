"""A geometry library cache stays under the private library-config folder.

The marker is CANARY_HIT. A library may write a cache there. A direct write
and an environment change are refused. The marker stays where it was written.
"""

from __future__ import annotations

import pytest

from aicad.sandbox import allow_design_scripts, run_design_script, sandbox_supported

_MARKER = "CANARY_HIT"


def test_a_library_cache_stays_private_and_a_script_cannot_redirect_it(tmp_path, monkeypatch):
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
        "from build123d import Box, export_obj\n"
        "os = pathlib.os\n"
        "home = pathlib.Path.home()\n"
        "cache = home / 'geom.cache'\n"
        "try:\n"
        "    cache.write_text('direct\\n', encoding='utf-8')\n"
        "    print('direct-wrote')\n"
        "except Exception as exc:\n"
        "    print('direct', type(exc).__name__)\n"
        "try:\n"
        "    export_obj(Box(1, 1, 1), str(cache))\n"
        "    print('library', cache.is_file())\n"
        "except Exception as exc:\n"
        "    print('library', type(exc).__name__)\n"
        "for name in ('HOME', 'MPLCONFIGDIR', 'XDG_CACHE_HOME', 'CSF_OCCTResourcePath'):\n"
        "    try:\n"
        "        os.environ[name] = 'other-cache'\n"
        "        print('set', name)\n"
        "    except Exception as exc:\n"
        "        print('set-blocked', name, type(exc).__name__)\n"
        "try:\n"
        "    os.environ = {}\n"
        "    print('rebound')\n"
        "except Exception as exc:\n"
        "    print('rebind-blocked', type(exc).__name__)\n"
        "try:\n"
        "    os.unsetenv('XDG_CACHE_HOME')\n"
        "    print('unset')\n"
        "except Exception as exc:\n"
        "    print('unset-blocked', type(exc).__name__)\n"
        "print('place', home)\n",
    )
    assert payload.get("ok") is True, payload.get("error")
    text = payload.get("stdout") or ""
    assert "direct-wrote" not in text
    assert "direct ScriptError" in text
    assert "library True" in text
    assert "set-blocked HOME PermissionError" in text
    assert "set-blocked MPLCONFIGDIR PermissionError" in text
    assert "set-blocked XDG_CACHE_HOME PermissionError" in text
    assert "set-blocked CSF_OCCTResourcePath PermissionError" in text
    assert "rebound" not in text.split()
    assert "rebind-blocked PermissionError" in text
    assert "unset" not in text.split()
    assert "unset-blocked PermissionError" in text
    assert "library-config" in text
    assert _MARKER not in text
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
    written = list(project.rglob("geom.cache"))
    assert len(written) == 1
    assert "library-config" in written[0].parts
    assert "Wavefront" in written[0].read_text(encoding="utf-8")
    assert "direct" not in written[0].read_text(encoding="utf-8")
