"""Sandbox findings from the 2026-10-21 bug hunt, as tests that must pass.

These are ordinary tests: a confined script must not clear the plugin gate,
signal its parent, leave a process behind, or use up a network grant it did
not successfully run with.
"""

from __future__ import annotations

import sys
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


def test_a_design_script_cannot_record_a_check_it_did_not_run(tmp_path, config_home):
    _sandbox_or_skip()
    allow_design_scripts(tmp_path)
    script = (
        "fn = document.list_parts\n"
        "found = False\n"
        "closure = getattr(fn, '__closure__', None)\n"
        "if closure:\n"
        "    for cell in closure:\n"
        "        value = cell.cell_contents\n"
        "        if isinstance(value, list):\n"
        "            value.append({'method': 'check_gears', 'result': {'ok': True}})\n"
        "            found = True\n"
        "for value in list(getattr(fn, '__globals__', {}).values()):\n"
        "    if isinstance(value, list):\n"
        "        value.append({'method': 'check_gears', 'result': {'ok': True}})\n"
        "        found = True\n"
        "try:\n"
        "    object.__getattribute__(document, '_state')\n"
        "    print('STATE')\n"
        "except Exception as exc:\n"
        "    print('STATE %s' % type(exc).__name__)\n"
        "try:\n"
        "    document.approve_plan()\n"
        "    print('APPROVED')\n"
        "except Exception as exc:\n"
        "    print('PLAN %s' % type(exc).__name__)\n"
        "try:\n"
        "    document.delete_part()\n"
        "except Exception as exc:\n"
        "    tb = exc.__traceback__\n"
        "    leaked = False\n"
        "    while tb is not None:\n"
        "        if type(tb.tb_frame.f_locals.get('self')).__name__ == 'Document':\n"
        "            leaked = True\n"
        "        tb = tb.tb_next\n"
        "    print('LEAK' if leaked else 'CLEAN')\n"
        "else:\n"
        "    print('DELETED')\n"
        "print(len(document.list_parts()))\n"
        "print('FOUND' if found else 'NONE')\n"
    )
    payload = run_design_script(tmp_path, script)
    assert payload.get("checks") == []
    stdout = str(payload.get("stdout"))
    assert "NONE" in stdout
    assert "STATE AttributeError" in stdout
    assert "PLAN AttributeError" in stdout
    assert "CLEAN" in stdout
    assert "APPROVED" not in stdout
    assert "DELETED" not in stdout


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
