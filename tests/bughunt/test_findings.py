"""Reproductions from the 2026-10-21 bug hunt.

A test that still carries xfail is a finding that is not fixed yet.
The others are the behavior the product should have, and they pass.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import AICADError
from aicad.sandbox import allow_design_scripts, run_design_script, run_part_script, sandbox_supported
from aicad.workspace import Workspace

BOX = [{"op": "add", "shape": "box", "size": [2, 2, 2]}]


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


@pytest.mark.xfail(
    reason="BH-S01: a design script can clear AICAD_SANDBOX_CHILD and skip the plugin consent gate",
    strict=False,
)
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


@pytest.mark.xfail(
    reason="BH-S02: a sandboxed script can signal its parent (signal 0 was delivered)",
    strict=False,
)
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


@pytest.mark.xfail(
    reason="BH-S03: a script can leave a detached child running after the tool returns",
    strict=False,
)
def test_bh_s03_script_cannot_leave_a_detached_child(tmp_path):
    _sandbox_or_skip()
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


def test_bh_s04_session_step_outside_the_project_is_not_imported(tmp_path):
    maker = tmp_path / "maker"
    maker.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    document = Document(maker)
    document.create_part_from_ops("box", [{"op": "add", "shape": "box", "size": [4, 4, 4]}])
    step = outside / "hidden.step"
    document.export("box", step, "step")
    victim = tmp_path / "victim"
    victim.mkdir()
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "stolen",
                "source": "step",
                "step_file": str(step.resolve()),
            }
        ],
    }
    (victim / "session.json").write_text(json.dumps(session), encoding="utf-8")
    reopened = Document(victim)
    info = reopened.list_parts()[0]
    assert info.built is False or info.volume_mm3 != pytest.approx(64)
    assert any("outside" in problem for problem in reopened.problems)

    nested = tmp_path / "nested"
    nested.mkdir()
    session["parts"][0]["step_file"] = "../outside/hidden.step"
    (nested / "session.json").write_text(json.dumps(session), encoding="utf-8")
    again = Document(nested)
    info = again.list_parts()[0]
    assert info.built is False or info.volume_mm3 != pytest.approx(64)
    assert any("outside" in problem for problem in again.problems)


def test_bh_s06_step_parser_stays_off_the_protocol_stream(tmp_path):
    bad = tmp_path / "not.step"
    bad.write_text("this is not a STEP file\n", encoding="utf-8")
    program = tmp_path / "import_bad.py"
    program.write_text(
        "from pathlib import Path\n"
        "from aicad.kernel import import_step_file\n"
        f"try:\n    import_step_file(Path({str(bad)!r}))\nexcept Exception:\n    pass\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(program)],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    assert "StepFile" not in combined
    assert "ERR" not in completed.stdout


def test_bh_f01_failed_place_does_not_poison_the_session(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops("plate", BOX)
    with pytest.raises(AICADError):
        document.place_part("plate", [float("nan"), 0.0, 0.0])
    document.create_part_from_ops("peg", [{"op": "add", "shape": "box", "size": [1, 1, 1]}])
    reopened = Document(tmp_path)
    assert reopened.measure("peg").volume_mm3 == pytest.approx(1)
    assert not math.isnan(reopened.measure("plate").bounding_box.min.x)


def test_bh_f02_thin_shell_does_not_pass_a_one_millimetre_wall(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "shell",
        [
            {"op": "add", "shape": "box", "size": [10, 10, 10]},
            {"op": "cut", "shape": "box", "size": [9.9, 9.9, 9.9]},
        ],
    )
    report = document.check_wall_thickness("shell", min_wall_mm=1.0, samples=4)
    assert report.estimated_min_mm is not None
    assert report.estimated_min_mm < 0.2 or report.passed is False


def test_bh_f03_thin_foil_does_not_pass_a_static_check(tmp_path):
    from aicad.fea import fea_available, solve_part
    from aicad import kernel

    available, reason = fea_available()
    if not available:
        pytest.skip(reason)
    shape = kernel.make_from_ops(
        [
            {"op": "add", "shape": "box", "size": [10, 10, 10]},
            {"op": "cut", "shape": "box", "size": [9.9, 9.9, 9.9]},
        ]
    )
    report = solve_part(
        shape,
        {
            "material": "pla",
            "mesh_size_mm": 2.0,
            "target_safety_factor": 2.0,
            "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
            "loads": [
                {
                    "name": "face",
                    "type": "force",
                    "vector": [0, 0, -20],
                    "select": {"kind": "plane", "normal": [1, 0, 0]},
                }
            ],
        },
        out_dir=tmp_path / "fea",
    )
    review = (report.review or {}).get("status")
    safety = report.min_safety_factor or 0
    assert not (report.status == "solved" and review == "pass" and safety > 20)


def test_bh_f04_bad_insert_does_not_print_a_traceback(tmp_path):
    aicad = Path(sys.executable).with_name("aicad")
    completed = subprocess.run(
        [
            str(aicad),
            "insert",
            "badbrg",
            "--kind",
            "ball_bearing",
            "--size",
            "nope",
            "--workspace",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "Traceback" not in completed.stderr
    assert completed.stderr.startswith("error:")


def test_bh_f05_corrupt_requirements_do_not_switch_the_open_project(tmp_path, config_home):
    home = tmp_path / "home"
    home.mkdir()
    workspace = Workspace(home)
    other = tmp_path / "other"
    other.mkdir()
    (other / "requirements.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(AICADError):
        workspace.open_project(other)
    assert workspace.root.resolve() == home.resolve()


@pytest.mark.xfail(
    reason="BH-F06: a design script that fails still consumes the one-shot network grant",
    strict=False,
)
def test_bh_f06_a_failed_script_keeps_the_network_grant(tmp_path, config_home):
    _sandbox_or_skip()
    allow_design_scripts(tmp_path)
    workspace = Workspace(tmp_path)
    workspace.allow_script_network_once()
    assert workspace.script_status()["network"] == "next run"
    workspace.execute("run_design_script", {"script": "raise RuntimeError('boom')"})
    assert workspace.script_status()["network"] == "next run"


def test_bh_f08_a_kilometer_box_is_rejected(tmp_path):
    document = Document(tmp_path)
    with pytest.raises(AICADError):
        document.create_part_from_ops("huge", [{"op": "add", "shape": "box", "size": [1e15, 1e15, 1e15]}])
    assert document.list_parts() == []
    reopened = Document(tmp_path)
    assert reopened.list_parts() == []


def test_bh_f07_opening_a_foreign_project_does_not_create_files(tmp_path, config_home):
    home = tmp_path / "home"
    home.mkdir()
    workspace = Workspace(home)
    other = tmp_path / "other"
    (other / "documents").mkdir(parents=True)
    (other / "documents" / "requirements.md").write_text("# Leave this\n", encoding="utf-8")
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "scripted",
                "source": "script",
                "script": "open('ran.txt','w').write('ran')\npart = Box(1,1,1)\n",
            }
        ],
    }
    (other / "session.json").write_text(json.dumps(session), encoding="utf-8")
    workspace.open_project(other)
    assert not (other / "documents" / "decisions.md").exists()
    assert not (other / "requirements.json").exists()
    assert (other / "documents" / "requirements.md").read_text(encoding="utf-8") == "# Leave this\n"
    assert not (other / "ran.txt").exists()
    assert workspace.document.list_parts()[0].built is False


def test_bh_f11_check_wall_accepts_min_wall(tmp_path):
    aicad = Path(sys.executable).with_name("aicad")
    completed = subprocess.run(
        [str(aicad), "check-wall", "missing", "--min-wall", "1.2", "--workspace", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "unrecognized arguments" not in completed.stderr
    assert completed.stderr.startswith("error:")


def test_bh_f14_cli_module_runs(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "aicad.cli", "insert", "badbrg", "--kind", "ball_bearing", "--size", "nope", "--workspace", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert completed.stderr.startswith("error:")
