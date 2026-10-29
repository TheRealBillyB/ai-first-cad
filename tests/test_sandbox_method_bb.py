"""Fontconfig does not read a configuration file from the account home.

The marker is CANARY_HIT. The process uses the file shipped with aicad.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aicad.paths import packaged_fontconfig_file, prepare_geometry_import

_MARKER = "CANARY_HIT"


def test_a_home_fontconfig_is_not_the_process_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    planted = home / ".fonts.conf"
    planted.parent.mkdir()
    planted.write_text(
        f"<fontconfig><dir>{_MARKER}</dir><include>{home / 'canary.conf'}</include></fontconfig>\n",
        encoding="utf-8",
    )
    nested = home / ".config" / "fontconfig"
    nested.mkdir(parents=True)
    (nested / "fonts.conf").write_text(f"<fontconfig><dir>{_MARKER}</dir></fontconfig>\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("FONTCONFIG_FILE", str(planted))
    prepare_geometry_import()
    chosen = Path(os.environ["FONTCONFIG_FILE"])
    assert chosen == packaged_fontconfig_file()
    text = chosen.read_text(encoding="utf-8")
    assert _MARKER not in text
    assert ".fonts.conf" not in text
    assert str(home) not in text
    if sys.platform == "win32":
        return
    # A process that already loaded fontconfig keeps that configuration.
    # A new process reads the file this process just selected.
    import subprocess

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctypes, os\n"
            "lib = ctypes.CDLL('libfontconfig.so.1')\n"
            "lib.FcInitLoadConfig.restype = ctypes.c_void_p\n"
            "lib.FcConfigGetConfigFiles.argtypes = [ctypes.c_void_p]\n"
            "lib.FcConfigGetConfigFiles.restype = ctypes.c_void_p\n"
            "lib.FcStrListNext.argtypes = [ctypes.c_void_p]\n"
            "lib.FcStrListNext.restype = ctypes.c_char_p\n"
            "config = lib.FcInitLoadConfig()\n"
            "names = lib.FcConfigGetConfigFiles(config)\n"
            "found = []\n"
            "while True:\n"
            "    item = lib.FcStrListNext(names)\n"
            "    if not item:\n"
            "        break\n"
            "    found.append(item.decode())\n"
            "print('\\n'.join(found))\n",
        ],
        env={**os.environ, "HOME": str(home), "FONTCONFIG_FILE": str(chosen)},
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0 and "libfontconfig" in probe.stderr:
        pytest.skip("fontconfig is not installed")
    assert probe.returncode == 0, probe.stderr
    loaded = probe.stdout
    assert str(chosen) in loaded
    assert str(planted) not in loaded
    assert _MARKER not in loaded
