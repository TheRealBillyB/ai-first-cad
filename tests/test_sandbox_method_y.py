"""A parent environment does not reach a script or a helper program.

The marker is CANARY_ENV_MARKER_VALUE. A startup file would write
SANDBOX_CANARY.txt if it ran.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from aicad.kernel import make_from_script
from aicad.sandbox import _child_env, program_environment

_MARKER = "CANARY_ENV_MARKER_VALUE"
_CANARY_TEXT = "CANARY_HIT\n"
_INJECTION = (
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_AUDIT",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "PYTHONSTARTUP",
    "PYTHONHOME",
    "PYTHONINSPECT",
)


def _upper_keys(env: dict[str, str]) -> set[str]:
    return {key.upper() for key in env}


def test_a_script_does_not_inherit_loader_or_startup_variables(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    project = tmp_path / "project"
    project.mkdir()
    startup = outside / "startup.py"
    startup.write_text(
        "open(" + repr(str(canary)) + ", 'w', encoding='utf-8').write(" + repr(_CANARY_TEXT) + ")\n",
        encoding="utf-8",
    )
    pylib = outside / "pylib"
    pylib.mkdir()
    (pylib / "sitecustomize.py").write_text(startup.read_text(encoding="utf-8"), encoding="utf-8")
    marker_bin = outside / "bin"
    marker_bin.mkdir()
    monkeypatch.setenv("LD_PRELOAD", str(outside / "libcanary.so"))
    monkeypatch.setenv("LD_LIBRARY_PATH", str(outside / "libs"))
    monkeypatch.setenv("LD_AUDIT", str(outside / "audit.so"))
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", str(outside / "libcanary.dylib"))
    monkeypatch.setenv("DYLD_LIBRARY_PATH", str(outside / "libs"))
    monkeypatch.setenv("DYLD_FRAMEWORK_PATH", str(outside / "frameworks"))
    monkeypatch.setenv("PYTHONSTARTUP", str(startup))
    monkeypatch.setenv("PYTHONHOME", str(outside / "pyhome"))
    monkeypatch.setenv("PYTHONINSPECT", "1")
    monkeypatch.setenv("PYTHONPATH", str(pylib))
    monkeypatch.setenv("AICAD_CANARY_MARKER", _MARKER)
    monkeypatch.setenv("PATH", str(marker_bin) + os.pathsep + os.environ.get("PATH", ""))
    shape = make_from_script("part = Box(1, 1, 1)\n", project)
    assert shape is not None
    assert not canary.exists()
    assert os.environ.get("AICAD_CANARY_MARKER") == _MARKER


def test_the_child_environment_is_scrubbed(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    marker_bin = outside / "bin"
    marker_bin.mkdir()
    pylib = outside / "pylib"
    pylib.mkdir()
    for name, value in {
        "LD_PRELOAD": str(outside / "libcanary.so"),
        "LD_LIBRARY_PATH": str(outside),
        "DYLD_INSERT_LIBRARIES": str(outside / "libcanary.dylib"),
        "DYLD_LIBRARY_PATH": str(outside),
        "PYTHONSTARTUP": str(outside / "startup.py"),
        "PYTHONHOME": str(outside / "pyhome"),
        "PYTHONPATH": str(pylib),
        "PATH": str(marker_bin),
    }.items():
        monkeypatch.setenv(name, value)
    env = _child_env(project, project / "job", network=False)
    upper = _upper_keys(env)
    for name in _INJECTION:
        assert name not in upper
    assert str(marker_bin) not in env.get("PATH", "")
    assert str(pylib) not in env.get("PYTHONPATH", "")
    program = program_environment()
    program_keys = _upper_keys(program)
    for name in _INJECTION:
        assert name not in program_keys
    assert "PYTHONPATH" not in program_keys
    assert str(marker_bin) not in program.get("PATH", "")


def test_calculix_and_ffmpeg_receive_the_scrubbed_environment(monkeypatch):
    from aicad import fea, modal

    seen: list[dict] = []

    def run(_command, **kwargs):
        seen.append(kwargs)

        class Done:
            returncode = 1
            stdout = ""
            stderr = b""

        return Done()

    monkeypatch.setattr(fea.subprocess, "run", run)
    monkeypatch.setattr(modal.subprocess, "run", run)
    monkeypatch.setenv("LD_PRELOAD", "libcanary.so")
    monkeypatch.setenv("PYTHONSTARTUP", "startup.py")
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: "ccx")
    import numpy as np

    mesh = {
        "points": np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        "elements": np.array([[0, 1, 2, 3]]),
        "order": 1,
    }
    forces = np.zeros((4, 3))
    forces[1, 0] = 1.0
    fea._run_ccx(mesh, np.array([0], dtype=np.int32), forces, {"youngs_modulus_mpa": 2000.0, "poisson": 0.3})
    monkeypatch.setattr(modal, "_frequency_deck", lambda *_args: "*HEADING\n")
    modal._frequency_once([], [], [], 1, 5)
    assert len(seen) == 2
    for item in seen:
        assert item.get("stdin") is subprocess.DEVNULL
        env = item.get("env") or {}
        upper = _upper_keys(env)
        assert "LD_PRELOAD" not in upper
        assert "PYTHONSTARTUP" not in upper
