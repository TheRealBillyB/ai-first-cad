"""Design scripts stay in the project, and opening a project does not run them.

These tests run on Linux, macOS, and Windows. Each machine has to confine the
probe with its own sandbox. A missing file does not count as confined.
"""

import asyncio
import json
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import ScriptError
from aicad.sandbox import allow_design_scripts, os_probe, run_design_script, run_part_script, script_log_path
from aicad.server import build_server
from aicad.workspace import COMMANDS, Workspace


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def test_windows_script_keeps_library_caches_in_the_project(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from aicad import sandbox as sandbox_mod

    font_cache = tmp_path / "font-cache"
    font_file = font_cache / "ezdxf" / "font_manager_cache.json"
    font_file.parent.mkdir(parents=True)
    font_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(sandbox_mod, "_windows_font_cache_home", lambda: font_cache)
    env = sandbox_mod._child_env(tmp_path, tmp_path / "job", False)
    cache = str(tmp_path / ".aicad" / "cache")
    assert env["XDG_CACHE_HOME"] == str(font_cache)
    assert env["XDG_CONFIG_HOME"] == cache
    assert env["XDG_DATA_HOME"] == cache
    assert env["MPLCONFIGDIR"] == cache
    assert env.get("USERPROFILE") != cache
    assert env.get("APPDATA") != cache
    assert env.get("LOCALAPPDATA") != cache
    assert env.get("HOME") != cache


def test_opening_a_session_does_not_run_its_script(tmp_path, config_home):
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "evil",
                "source": "script",
                "script": "open('marker.txt', 'w').write('ran')\npart = Box(1, 1, 1)\n",
            }
        ],
    }
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")
    document = Document(tmp_path)
    assert not (tmp_path / "marker.txt").exists()
    info = document.list_parts()[0]
    assert info.name == "evil"
    assert info.built is False
    source = document.get_source("evil")
    assert source["script_pending"] is True
    assert "Box" in source["script"]
    with pytest.raises(Exception, match="has not been run"):
        document.measure("evil")


def test_script_geometry_reopens_without_running_again(tmp_path, config_home):
    document = Document(tmp_path)
    script = 'block = feature.add("base", Box(4, 4, 4))\npart = block\n'
    document.create_part_from_script("block", script)
    assert document.feature_report("block").tracked is True
    assert document.feature_report("block").steps[0].name == "base"
    reopened = Document(tmp_path)
    assert not (tmp_path / "marker.txt").exists()
    report = reopened.feature_report("block")
    assert report.tracked is True
    assert report.steps[0].name == "base"
    assert reopened.measure("block").volume_mm3 == pytest.approx(64)
    assert reopened.get_source("block")["script_pending"] is False


def test_open_project_loads_script_text_and_does_not_run_it(tmp_path, config_home):
    other = tmp_path / "other"
    other.mkdir()
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "evil",
                "source": "script",
                "script": "open('marker.txt', 'w').write('ran')\npart = Box(1, 1, 1)\n",
            }
        ],
    }
    (other / "session.json").write_text(json.dumps(session), encoding="utf-8")
    workspace = Workspace(tmp_path / "here")
    opened = workspace.execute("open_project", {"path": str(other)})
    assert opened["parts"] == ["evil"]
    assert workspace.document.list_parts()[0].built is False
    assert not (other / "marker.txt").exists()
    saved = workspace.execute("save_project", {})
    saved_path = Path(saved["path"])
    session_file = saved_path if saved_path.name == "session.json" else saved_path / "session.json"
    assert session_file.is_file()
    assert workspace.execute("design_script_status", {})["enabled"] is False


def test_design_script_cannot_allow_a_plugin(tmp_path, config_home, monkeypatch):
    consent = tmp_path / "consent" / "plugins.json"
    monkeypatch.setenv("AICAD_PLUGIN_CONSENT", str(consent))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    allow_design_scripts(tmp_path)
    script = """
import aicad.plugins as plugins
print("consent_path", plugins.consent_file())
try:
    plugins.allow("anything", accept=True)
    print("allow ran")
except Exception as exc:
    print("allow", type(exc).__name__)
    print(exc)
try:
    plugins.load("anything")
    print("load ran")
except Exception as exc:
    print("load", type(exc).__name__)
    print(exc)
try:
    plugins.revoke("anything")
    print("revoke ran")
except Exception as exc:
    print("revoke", type(exc).__name__)
    print(exc)
"""
    payload = run_design_script(tmp_path, script)
    assert payload["ok"] is True, payload.get("error")
    text = payload["stdout"]
    assert "consent_path" in text
    assert str(tmp_path) not in text.split("consent_path", 1)[1].splitlines()[0]
    for name in ("allow", "load", "revoke"):
        assert f"{name} PluginConsentError" in text
    assert "cannot" in text
    assert not consent.exists()
    assert not (tmp_path / "xdg" / "aicad" / "plugins.json").exists()


def test_design_script_requires_opt_in(tmp_path, config_home):
    with pytest.raises(ScriptError, match="allow-scripts"):
        run_design_script(tmp_path, "print('no')\n")
    assert not script_log_path(tmp_path).exists()


def test_import_and_paths_are_refused(tmp_path, config_home):
    allow_design_scripts(tmp_path)
    script = """
errors = []
try:
    import os
except Exception as exc:
    errors.append(str(exc))
try:
    open("/etc/passwd", encoding="utf-8")
except Exception as exc:
    errors.append(str(exc))
try:
    open("/tmp/aicad-sandbox-outside.txt", "w", encoding="utf-8")
except Exception as exc:
    errors.append(str(exc))
open("inside.txt", "w", encoding="utf-8").write("ok")
print(" | ".join(errors))
"""
    payload = run_design_script(tmp_path, script)
    assert payload["ok"] is True, payload
    text = payload["stdout"]
    assert "not allowed" in text
    assert "outside the project" in text
    assert "/etc/passwd" in text
    assert "/tmp/aicad-sandbox-outside.txt" in text
    assert (tmp_path / "inside.txt").read_text(encoding="utf-8") == "ok"
    assert not Path("/tmp/aicad-sandbox-outside.txt").exists()
    log = script_log_path(tmp_path).read_text(encoding="utf-8")
    assert "import os" in log
    assert '"ok": true' in log


# EPERM, EACCES, ENETUNREACH, and the Windows access-denied socket codes.
# ENOENT (2) is a missing path, not a sandbox.
_CONFINED_ERRNOS = {1, 13, 101, 10013, 10051}


def _assert_confined(label: str) -> None:
    assert label.startswith("denied:"), label
    code = int(label.rsplit(":", 1)[1])
    assert code in _CONFINED_ERRNOS, label


@pytest.mark.sandbox
def test_os_probe_blocks_network_and_outside_files(tmp_path, config_home):
    probe = os_probe(tmp_path)
    _assert_confined(probe["read_outside"])
    _assert_confined(probe["write_outside"])
    assert probe["write_inside"] == "ok"
    _assert_confined(probe["tcp"])
    _assert_confined(probe["udp"])
    if sys.platform.startswith("linux"):
        assert probe["mechanism"] == "landlock+seccomp"
    elif sys.platform == "darwin":
        assert probe["mechanism"] == "seatbelt"
    elif sys.platform == "win32":
        assert probe["mechanism"] == "appcontainer+job"


@pytest.mark.sandbox_linux
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux Landlock sandbox")
def test_linux_sandbox_is_landlock_and_seccomp(tmp_path, config_home):
    probe = os_probe(tmp_path)
    assert probe["mechanism"] == "landlock+seccomp"
    _assert_confined(probe["read_outside"])
    _assert_confined(probe["udp"])


@pytest.mark.sandbox_macos
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt sandbox")
def test_macos_sandbox_is_seatbelt(tmp_path, config_home):
    probe = os_probe(tmp_path)
    assert probe["mechanism"] == "seatbelt"
    _assert_confined(probe["read_outside"])
    _assert_confined(probe["tcp"])


@pytest.mark.sandbox_windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer sandbox")
def test_windows_sandbox_is_an_appcontainer(tmp_path, config_home):
    probe = os_probe(tmp_path)
    assert probe["mechanism"] == "appcontainer+job"
    _assert_confined(probe["read_outside"])
    _assert_confined(probe["tcp"])


@pytest.mark.sandbox_windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows process rights")
def test_windows_script_cannot_open_its_parent(tmp_path, config_home):
    """Direct OpenProcess / TerminateProcess on this aicad process is denied."""
    script = (
        "import ctypes\n"
        "from ctypes import wintypes\n"
        "import aicad.sandbox as sandbox\n"
        "kernel = ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]\n"
        "kernel.OpenProcess.restype = wintypes.HANDLE\n"
        "kernel.CloseHandle.argtypes = [wintypes.HANDLE]\n"
        "rights = 0x0001 | 0x1000\n"
        "handle = kernel.OpenProcess(rights, False, sandbox.os.getppid())\n"
        "if handle:\n"
        "    kernel.CloseHandle(handle)\n"
        "    open('open.txt', 'w', encoding='utf-8').write('opened')\n"
        "else:\n"
        "    open('open.txt', 'w', encoding='utf-8').write('denied:%s' % ctypes.get_last_error())\n"
        "part = Box(1, 1, 1)\n"
    )
    run_part_script(script, tmp_path)
    assert (tmp_path / "open.txt").read_text(encoding="utf-8").startswith("denied")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux cgroup pids controller")
def test_linux_process_cap_is_a_cgroup_when_this_user_can_make_one():
    """The cap has to be a sibling cgroup. The user-wide limit counts other programs."""
    from aicad.sandbox import _PROCESS_CAP, _prepare_pids_cgroup, _release_pids_cgroup

    path = _prepare_pids_cgroup()
    if path is None:
        pytest.skip("this user cannot create a cgroup pids.max without root")
    try:
        assert (path / "pids.max").read_text(encoding="utf-8").strip() == str(_PROCESS_CAP)
        assert path.name not in Path("/proc/self/cgroup").read_text(encoding="utf-8")
    finally:
        _release_pids_cgroup(path)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux counts every user thread toward RLIMIT_NPROC")
def test_script_finishes_while_the_user_already_has_many_threads(tmp_path):
    """A desktop with a browser open is already past 256 threads. Geometry still has to finish."""
    from aicad.kernel import measure

    stop = threading.Event()
    threads: list[threading.Thread] = []

    def park() -> None:
        stop.wait()

    try:
        for _ in range(300):
            thread = threading.Thread(target=park, daemon=True)
            try:
                thread.start()
            except RuntimeError:
                pytest.skip("this user cannot start 300 extra threads")
            threads.append(thread)
        shape = run_part_script(
            "text = open('/proc/self/limits', encoding='utf-8').read()\n"
            "open('limits.txt', 'w', encoding='utf-8').write(text)\n"
            "part = Box(1, 1, 1)\n",
            tmp_path,
        )
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=1)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    soft = None
    for line in (tmp_path / "limits.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("Max processes"):
            soft = line.split()[2]
            break
    assert soft is not None
    assert soft == "unlimited" or int(soft) > 256


def test_wall_clock_limit_stops_the_script(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "2")
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "30")
    with pytest.raises(ScriptError, match="longer than 2 seconds"):
        run_part_script("while True:\n    pass\n", tmp_path)


def test_cpu_or_memory_limit_stops_the_script(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "1")
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "30")
    with pytest.raises(ScriptError, match="memory or CPU"):
        run_part_script("while True:\n    pass\n", tmp_path)


def test_memory_limit_stops_a_huge_allocation(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_MEMORY_MB", "256")
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "60")
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "30")
    with pytest.raises(ScriptError, match="MemoryError|memory or CPU"):
        run_part_script("blob = bytearray(800 * 1024 * 1024)\npart = Box(1, 1, 1)\n", tmp_path)


def test_design_script_reports_the_change_and_the_check(tmp_path, config_home):
    allow_design_scripts(tmp_path)
    script = """
document.create_part_from_script("plate", "part = Box(20, 10, 1)\\n")
wall = document.check_wall_thickness("plate", 2)
print(wall.passed)
document.update_part_script("plate", "part = Box(20, 10, 4)\\n")
wall = document.check_wall_thickness("plate", 2)
print(wall.passed)
"""
    payload = run_design_script(tmp_path, script)
    assert payload["ok"] is True, payload.get("error")
    assert payload["changed_parts"]
    assert payload["changed_parts"][0]["name"] == "plate"
    assert payload["changed_parts"][0]["volume_mm3"] == pytest.approx(800)
    methods = [item["method"] for item in payload["checks"]]
    assert methods.count("check_wall_thickness") == 2
    assert "False" in payload["stdout"]
    assert "True" in payload["stdout"]
    assert payload["views"]
    assert Path(payload["views"][0]["path"]).is_file()
    reopened = Document(tmp_path)
    assert reopened.measure("plate").volume_mm3 == pytest.approx(800)


def test_network_switch_is_not_an_assistant_command(tmp_path, config_home, monkeypatch):
    workspace = Workspace(tmp_path)
    assert workspace.script_status()["network"] == "off"
    workspace.allow_script_network_once()
    assert workspace.script_status()["network"] == "next run"
    assert workspace.consume_script_network() is True
    assert workspace.consume_script_network() is False
    monkeypatch.setenv("AICAD_SCRIPT_NETWORK", "1")
    always = Workspace(tmp_path)
    assert always.script_status()["network"] == "on"
    assert "allow_script_network_once" not in COMMANDS


def test_windows_grant_does_not_touch_the_volume_root(tmp_path, monkeypatch):
    """Walking up to a project must not run icacls on ``/`` or ``C:\\``.

    That call has no useful grant and can sit until the tool is killed.
    """

    from aicad import sandbox_windows

    granted: list[Path] = []

    def record(path, _sid, _rights, *, recurse):
        granted.append(Path(path))
        assert recurse is False

    monkeypatch.setattr(sandbox_windows, "_icacls", record)
    sandbox_windows._grant_traverse(tmp_path, "S-1-5-21-1")
    sandbox_windows._grant_tree(Path(tmp_path.anchor), "S-1-5-21-1", "(OI)(CI)RX")
    assert granted
    assert all(not sandbox_windows._is_volume_root(path) for path in granted)


def test_icacls_refuses_a_volume_root_and_stops_waiting(tmp_path, monkeypatch):
    from aicad import sandbox_windows

    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 0))

    monkeypatch.setattr(sandbox_windows.subprocess, "run", fake_run)
    with pytest.raises(OSError, match="volume root"):
        sandbox_windows._icacls(Path(tmp_path.anchor), "S-1-5-21-1", "(RX)", recurse=False)
    assert calls == []
    with pytest.raises(OSError, match="did not finish"):
        sandbox_windows._icacls(tmp_path, "S-1-5-21-1", "(RX)", recurse=False)
    assert calls[0]["timeout"] == sandbox_windows._ICACLS_TIMEOUT_SECONDS


def test_reclaim_skips_a_volume_root(tmp_path, monkeypatch):
    from aicad import sandbox_windows

    def fail(*_args, **_kwargs):
        raise AssertionError("icacls should not run on a volume root")

    monkeypatch.setattr(sandbox_windows.subprocess, "run", fail)
    sandbox_windows._reclaim_for_user(Path(tmp_path.anchor))


def test_unexpected_errors_become_tool_errors(tmp_path, config_home):
    from mcp.server.mcpserver.exceptions import ToolError

    saved = COMMANDS["list_parts"]
    COMMANDS["list_parts"] = replace(saved, handler=lambda _workspace, _arguments: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        server = build_server(tmp_path)
        with pytest.raises(ToolError, match="RuntimeError: boom"):
            asyncio.run(server.call_tool("list_parts", {}))
    finally:
        COMMANDS["list_parts"] = saved
