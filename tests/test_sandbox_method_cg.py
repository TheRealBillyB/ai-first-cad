"""CalculiX, ffmpeg, and a slicer stay inside the helper folder.

The marker is CANARY_HIT. A helper can read a file in its folder. It cannot
read the marker outside that folder, and the marker is not changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aicad.sandbox import program_environment, run_confined_helper, sandbox_supported

_MARKER = "CANARY_HIT"


def test_a_confined_helper_cannot_read_outside_its_folder(tmp_path):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    script = work / "helper.py"
    script.write_text(
        "import pathlib, sys\n"
        "outside = sys.argv[1]\n"
        "try:\n"
        "    inside = pathlib.Path('inside.txt').read_text(encoding='utf-8')\n"
        "except Exception as exc:\n"
        "    inside = type(exc).__name__\n"
        "try:\n"
        "    leaked = pathlib.Path(outside).read_text(encoding='utf-8')\n"
        "except Exception as exc:\n"
        "    leaked = type(exc).__name__\n"
        "pathlib.Path('result.txt').write_text(inside + '|' + leaked, encoding='utf-8')\n",
        encoding="utf-8",
    )
    (work / "inside.txt").write_text("inside-ok\n", encoding="utf-8")
    completed = run_confined_helper(
        [sys.executable, str(script), str(marker)],
        env=program_environment(),
        timeout=60,
        cwd=work,
    )
    result_path = work / "result.txt"
    assert result_path.is_file(), completed.stderr
    result = result_path.read_text(encoding="utf-8")
    assert result.startswith("inside-ok"), result
    assert _MARKER not in result
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"


def test_the_solver_helpers_use_the_confined_runner():
    import aicad.animation as animation
    import aicad.fea as fea
    import aicad.modal as modal
    import aicad.printprep as printprep
    import inspect

    for module in (fea, modal, animation, printprep):
        assert "run_confined_helper" in inspect.getsource(module)
        assert "subprocess.run" not in inspect.getsource(module)
