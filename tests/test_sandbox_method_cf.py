"""A design script cannot retarget library config through an imported module.

The marker is CANARY_HIT. A lower-level environment call is refused. A
script that only computes still runs, and that marker stays where it was written.
"""

from __future__ import annotations

import pytest

from aicad.sandbox import allow_design_scripts, run_design_script, sandbox_supported

_MARKER = "CANARY_HIT"


def test_a_script_cannot_retarget_home_through_an_imported_module(tmp_path, monkeypatch):
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
    plain = run_design_script(project, "print(2 + 2)\n")
    assert plain.get("ok") is True, plain.get("error")
    assert "4" in (plain.get("stdout") or "")
    payload = run_design_script(
        project,
        "import aicad.paths as paths\n"
        "import aicad.sandbox as sandbox\n"
        "modules = (paths, sandbox)\n"
        "for module in modules:\n"
        "    if module.os.name == 'nt':\n"
        "        kernel = module.ctypes.WinDLL('kernel32')\n"
        "        kernel.SetEnvironmentVariableW.argtypes = [module.ctypes.c_wchar_p, module.ctypes.c_wchar_p]\n"
        "        try:\n"
        "            kernel.SetEnvironmentVariableW('HOME', r'C:\\canary-home')\n"
        "            print('changed')\n"
        "        except Exception as exc:\n"
        "            print('blocked', type(exc).__name__)\n"
        "    else:\n"
        "        libc = module.ctypes.CDLL(None)\n"
        "        libc.setenv.argtypes = [module.ctypes.c_char_p, module.ctypes.c_char_p, module.ctypes.c_int]\n"
        "        libc.setenv.restype = module.ctypes.c_int\n"
        "        try:\n"
        "            libc.setenv(b'HOME', b'/tmp/canary-home', 1)\n"
        "            print('changed')\n"
        "        except Exception as exc:\n"
        "            print('blocked', type(exc).__name__)\n"
        "print('home', __import__('pathlib').Path.home())\n",
    )
    assert payload.get("ok") is True, payload.get("error")
    text = payload.get("stdout") or ""
    assert "changed" not in text.split()
    assert "blocked" in text
    assert "canary-home" not in text
    assert "library-config" in text
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
