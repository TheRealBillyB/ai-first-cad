"""A design script receives an allowlist, not the parent environment.

The marker is CANARY_ENV_MARKER_VALUE. A script that can read its own
environment writes that marker only when the name was copied.
"""

from __future__ import annotations

import os
import sys

import pytest

from aicad.sandbox import _OS_ENV, _SCRIPT_LIMITS, _SCRIPT_LOCALE, _SCRIPT_TEXT, _child_env

_MARKER = "CANARY_ENV_MARKER_VALUE"

_DROPPED = (
    "ORDINARY_PARENT",
    "HOME",
    "USER",
    "LOGNAME",
    "APPDATA",
    "LOCALAPPDATA",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "LD_PRELOAD",
    "PYTHONSTARTUP",
    "FONTCONFIG_FILE",
    "GMSH_HOME",
    "AICAD_CONFIG",
    "AICAD_SETTINGS",
    "SECRET_TOKEN",
)


def _allowed_names() -> set[str]:
    names = {
        "PATH",
        "PYTHONSAFEPATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONPATH",
        "AICAD_SANDBOX_CHILD",
        "AICAD_WORKSPACE",
        "AICAD_JOB",
        "AICAD_NETWORK",
        "TMPDIR",
        "TMP",
        "TEMP",
        *_SCRIPT_LOCALE,
        *_SCRIPT_TEXT,
        *_SCRIPT_LIMITS,
        *_OS_ENV,
    }
    if sys.platform == "win32":
        names.update(
            {
                "USERPROFILE",
                "AICAD_WIN_NET_RECEIVERS",
                "XDG_CACHE_HOME",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "MPLCONFIGDIR",
            }
        )
    return names


def test_a_script_environment_is_an_allowlist(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker_bin = outside / "bin"
    marker_bin.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    for name in _DROPPED:
        monkeypatch.setenv(name, _MARKER)
    monkeypatch.setenv("PATH", str(marker_bin) + os.pathsep + "kept")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "1")
    monkeypatch.setenv("AICAD_SCRIPT_MEMORY_MB", "256")
    monkeypatch.setenv("AICAD_SCRIPT_FILE_BYTES", "2048")
    monkeypatch.setenv("AICAD_WIN_NET_RECEIVERS", "127.0.0.1:9:9")
    monkeypatch.setenv("USERPROFILE", str(outside / "profile"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(outside / "config"))
    monkeypatch.setenv("TEMP", str(outside / "temp"))
    monkeypatch.setenv("TMP", str(outside / "temp"))
    env = _child_env(project, project / "job", network=False)
    upper = {key.upper() for key in env}
    for name in _DROPPED:
        assert name not in upper
    assert set(env) <= _allowed_names()
    assert _MARKER not in env.values()
    assert str(marker_bin) not in env["PATH"]
    assert env["LANG"] == "C"
    assert env["TZ"] == "UTC"
    assert env["AICAD_SCRIPT_CPU_SECONDS"] == "1"
    assert env["AICAD_SCRIPT_MEMORY_MB"] == "256"
    assert env["AICAD_SCRIPT_FILE_BYTES"] == "2048"
    assert env["AICAD_SANDBOX_CHILD"] == "1"
    assert env["AICAD_WORKSPACE"] == str(project)
    assert env["AICAD_JOB"] == str(project / "job")
    assert env["AICAD_NETWORK"] == "0"
    assert env["PYTHONSAFEPATH"] == "1"
    assert env["TEMP"] == str(project / ".aicad" / "tmp")
    assert env["TMP"] == str(project / ".aicad" / "tmp")
    assert env["TMPDIR"] == str(project / ".aicad" / "tmp")
    if sys.platform == "win32":
        assert env["USERPROFILE"] == str(outside / "profile")
        assert env["AICAD_WIN_NET_RECEIVERS"] == "127.0.0.1:9:9"
        assert env["XDG_CONFIG_HOME"] == str(project / ".aicad" / "cache")
    else:
        assert "USERPROFILE" not in env
        assert "AICAD_WIN_NET_RECEIVERS" not in env
        assert "XDG_CONFIG_HOME" not in env
        assert "HOME" not in env


def test_windows_script_environment_keeps_the_profile_and_receivers(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from aicad import sandbox as sandbox_mod

    font_cache = tmp_path / "font-cache"
    font_file = font_cache / "ezdxf" / "font_manager_cache.json"
    font_file.parent.mkdir(parents=True)
    font_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(sandbox_mod, "_windows_font_cache_home", lambda: font_cache)
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("ORDINARY_PARENT", _MARKER)
    monkeypatch.setenv("HOME", _MARKER)
    monkeypatch.setenv("APPDATA", _MARKER)
    monkeypatch.setenv("LOCALAPPDATA", _MARKER)
    monkeypatch.setenv("USERPROFILE", str(outside / "profile"))
    monkeypatch.setenv("AICAD_WIN_NET_RECEIVERS", "10.0.0.1:80:80")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(outside / "config"))
    env = sandbox_mod._child_env(project, project / "job", False)
    cache = str(project / ".aicad" / "cache")
    assert set(env) <= _allowed_names()
    assert env["USERPROFILE"] == str(outside / "profile")
    assert env["USERPROFILE"] != cache
    assert env["AICAD_WIN_NET_RECEIVERS"] == "10.0.0.1:80:80"
    assert env["XDG_CACHE_HOME"] == str(font_cache)
    assert env["XDG_CONFIG_HOME"] == cache
    assert env["XDG_DATA_HOME"] == cache
    assert env["MPLCONFIGDIR"] == cache
    assert "ORDINARY_PARENT" not in env
    assert "HOME" not in env
    assert "APPDATA" not in env
    assert "LOCALAPPDATA" not in env
    assert _MARKER not in env.values()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux process environment")
def test_a_running_script_does_not_see_a_parent_variable(tmp_path, monkeypatch):
    from aicad.kernel import make_from_script
    from aicad.sandbox import sandbox_supported

    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    monkeypatch.setenv("ORDINARY_PARENT", _MARKER)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    project = tmp_path / "project"
    project.mkdir()
    make_from_script(
        "raw = open('/proc/self/environ', 'rb').read()\n"
        "open('env.bin', 'wb').write(raw)\n"
        "part = Box(1, 1, 1)\n",
        project,
    )
    seen = (project / "env.bin").read_bytes()
    assert _MARKER.encode() not in seen
    assert b"ORDINARY_PARENT" not in seen
    assert b"HTTP_PROXY" not in seen
    assert b"AICAD_SANDBOX_CHILD=1" in seen
    assert b"AICAD_WORKSPACE=" in seen
