"""Sandbox findings from the 2026-10-21 bug hunt, as tests that must pass.

These are ordinary tests: a confined script must not clear the plugin gate,
signal its parent, leave a process behind, or use up a network grant it did
not successfully run with.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from aicad.sandbox import allow_design_scripts, run_design_script, run_part_script, sandbox_supported
from aicad.workspace import Workspace


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def test_an_older_landlock_abi_still_runs_a_script(monkeypatch):
    class Libc:
        def __init__(self, abi: int):
            self.abi = abi

        def syscall(self, *_args):
            return self.abi

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("aicad.sandbox._arch", lambda: "x86_64")
    for abi, supported in ((3, False), (4, True), (5, True), (6, True), (7, True)):
        monkeypatch.setattr("aicad.sandbox._libc", lambda abi=abi: Libc(abi))
        ok, reason = sandbox_supported()
        assert ok is supported
        if supported:
            assert reason == ""
        else:
            assert "signal" not in reason


def test_the_process_filter_refuses_a_signal_to_another_process(tmp_path):
    if not sys.platform.startswith("linux"):
        pytest.skip("the signal filter is the Linux process limit")
    report = tmp_path / "signals.txt"
    script = textwrap.dedent(
        """
        import ctypes
        import os

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise SystemExit("could not lock the process")
        from aicad.sandbox import _GETTID, _ISOLATION_SYSCALLS, _SIGNAL_SYSCALLS, _arch, _block_sockets

        _block_sockets(network=False)
        arch = _arch()
        libc.syscall.restype = ctypes.c_long
        pid = os.getpid()
        tid = int(libc.syscall(_GETTID[arch]))
        parent = os.getppid()

        def once(label, number, *args):
            ctypes.set_errno(0)
            result = libc.syscall(number, *args)
            if result == 0:
                return f"{label}:allowed"
            return f"{label}:denied:{ctypes.get_errno()}"

        signals = _SIGNAL_SYSCALLS[arch]
        pidfd = next(number for number in _ISOLATION_SYSCALLS[arch] if number == 424)
        lines = [
            once("self-kill", signals["kill"], pid, 0),
            once("parent-kill", signals["kill"], parent, 0),
            once("init-kill", signals["kill"], 1, 0),
            once("group-kill", signals["kill"], 0, 0),
            once("self-tkill", signals["tkill"], tid, 0),
            once("parent-tkill", signals["tkill"], parent, 0),
            once("self-tgkill", signals["tgkill"], pid, tid, 0),
            once("parent-tgkill", signals["tgkill"], parent, parent, 0),
            once("self-rt", signals["rt_sigqueueinfo"], pid, 0, 0),
            once("parent-rt", signals["rt_sigqueueinfo"], parent, 0, 0),
            once("self-rt-tg", signals["rt_tgsigqueueinfo"], pid, tid, 0, 0),
            once("parent-rt-tg", signals["rt_tgsigqueueinfo"], parent, parent, 0, 0),
            once("pidfd", pidfd, 0, 0, 0, 0),
        ]
        open(os.environ["AICAD_SIGNAL_REPORT"], "w", encoding="utf-8").write("\\n".join(lines) + "\\n")
        """
    )
    env = os.environ.copy()
    env["AICAD_SIGNAL_REPORT"] = str(report)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    text = report.read_text(encoding="utf-8")
    rows = dict(line.split(":", 1) for line in text.splitlines())
    assert rows["self-kill"] == "allowed"
    assert rows["self-tkill"] == "allowed"
    assert rows["self-tgkill"] == "allowed"
    for label in ("self-rt", "self-rt-tg"):
        assert rows[label] != "denied:1"
    refused = (
        "parent-kill",
        "init-kill",
        "group-kill",
        "parent-tkill",
        "parent-tgkill",
        "parent-rt",
        "parent-rt-tg",
        "pidfd",
    )
    for label in refused:
        assert rows[label] == "denied:1"


def test_a_script_cannot_signal_outside_its_own_process(tmp_path):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux applies the signal filter in the kernel")
    _sandbox_or_skip()
    script = (
        "import aicad.sandbox as sandbox\n"
        "def once(label, pid):\n"
        "    try:\n"
        "        sandbox.os.kill(pid, 0)\n"
        "    except OSError as exc:\n"
        "        return '%s:denied:%s' % (label, exc.errno)\n"
        "    return '%s:allowed' % label\n"
        "open('signals.txt', 'w', encoding='utf-8').write('\\n'.join([\n"
        "    once('self', sandbox.os.getpid()),\n"
        "    once('parent', sandbox.os.getppid()),\n"
        "    once('init', 1),\n"
        "    once('group', 0),\n"
        "]) + '\\n')\n"
        "part = Box(1, 1, 1)\n"
    )
    run_part_script(script, tmp_path)
    text = (tmp_path / "signals.txt").read_text(encoding="utf-8")
    assert "self:allowed" in text
    assert "parent:denied:" in text
    assert "init:denied:" in text
    assert "group:denied:" in text


def test_bh_s01_design_script_cannot_clear_the_plugin_gate(tmp_path, config_home):
    _sandbox_or_skip()
    allow_design_scripts(tmp_path)
    script = (
        "import aicad.sandbox as sandbox\n"
        "sandbox.os.environ['AICAD_SANDBOX_CHILD'] = '0'\n"
        "import aicad.plugins as plugins\n"
        "try:\n"
        "    plugins.allow('no-such-plugin', accept=True)\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__)\n"
    )
    payload = run_design_script(tmp_path, script)
    assert "PluginConsentError" in str(payload.get("stdout"))


def test_bh_s02_script_cannot_signal_its_parent(tmp_path):
    _sandbox_or_skip()
    script = (
        "import aicad.sandbox as sandbox\n"
        "try:\n"
        "    sandbox.os.kill(sandbox.os.getppid(), 0)\n"
        "    open('signal.txt', 'w').write('delivered')\n"
        "except OSError as exc:\n"
        "    open('signal.txt', 'w').write('denied:%s' % exc.errno)\n"
        "part = Box(1, 1, 1)\n"
    )
    run_part_script(script, tmp_path)
    assert (tmp_path / "signal.txt").read_text(encoding="utf-8").startswith("denied")


def test_bh_s03_script_cannot_leave_a_detached_child(tmp_path):
    _sandbox_or_skip()
    if sys.platform == "win32":
        # Windows has no fork. A second Python process is the same kind of
        # leftover: it sleeps, then writes the file, unless the job kills it.
        script = (
            "import aicad.sandbox as sandbox\n"
            "code = (\n"
            "    'import time\\n'\n"
            "    'from pathlib import Path\\n'\n"
            "    'time.sleep(8)\\n'\n"
            "    'Path(\"phase2.txt\").write_text(\"survived\\\\n\", encoding=\"utf-8\")\\n'\n"
            ")\n"
            "sandbox.subprocess.Popen([sandbox.sys.executable, '-c', code])\n"
            "part = Box(1, 1, 1)\n"
        )
    else:
        script = (
            "import aicad.sandbox as sandbox\n"
            "pid = sandbox.os.fork()\n"
            "if pid == 0:\n"
            "    sandbox.os.setsid()\n"
            "    \n"
            "    for fd in (0, 1, 2):\n"
            "        try:\n"
            "            sandbox.os.close(fd)\n"
            "        except OSError:\n"
            "            pass\n"
            "    sandbox.os.system('sleep 2')\n"
            "    open('phase2.txt', 'w').write('survived\\n')\n"
            "    sandbox.os._exit(0)\n"
            "part = Box(1, 1, 1)\n"
        )
    run_part_script(script, tmp_path)
    time.sleep(4)
    assert not (tmp_path / "phase2.txt").exists()


def test_bh_f06_a_failed_script_keeps_the_network_grant(tmp_path, config_home):
    _sandbox_or_skip()
    allow_design_scripts(tmp_path)
    workspace = Workspace(tmp_path)
    workspace.allow_script_network_once()
    assert workspace.script_status()["network"] == "next run"
    workspace.execute("run_design_script", {"script": "raise RuntimeError('boom')"})
    assert workspace.script_status()["network"] == "next run"
