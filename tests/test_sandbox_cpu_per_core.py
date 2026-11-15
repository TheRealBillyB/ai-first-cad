"""The script CPU cap is per core.

The operating system counts the CPU time of every thread in the script, and
OpenCascade runs a boolean on all cores. A flat 60 s cap stopped a lead screw
that took 4.6 s on the wall clock (85 s of CPU on 24 cores).
"""

from __future__ import annotations

from aicad import sandbox
from aicad.errors import ScriptError


def test_the_cpu_cap_is_sixty_seconds_for_each_core(monkeypatch):
    monkeypatch.delenv("AICAD_SCRIPT_CPU_SECONDS", raising=False)
    monkeypatch.setattr(sandbox, "script_cores", lambda: 24)
    assert sandbox.cpu_seconds() == 24 * sandbox.CPU_SECONDS == 1440
    monkeypatch.setattr(sandbox, "script_cores", lambda: 1)
    assert sandbox.cpu_seconds() == sandbox.CPU_SECONDS


def test_the_environment_lowers_the_cpu_cap_and_cannot_raise_it(monkeypatch):
    monkeypatch.setattr(sandbox, "script_cores", lambda: 4)
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "30")
    assert sandbox.cpu_seconds() == 30
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "999999")
    assert sandbox.cpu_seconds() == 4 * sandbox.CPU_SECONDS


def test_a_script_gets_the_cap_the_parent_counted(monkeypatch, tmp_path):
    """The confined child may not be able to count cores, so it is told the cap."""
    monkeypatch.delenv("AICAD_SCRIPT_CPU_SECONDS", raising=False)
    monkeypatch.setattr(sandbox, "script_cores", lambda: 12)
    project = tmp_path / "project"
    project.mkdir()
    env = sandbox._child_env(project, project / "job", network=False)
    assert env["AICAD_SCRIPT_CPU_SECONDS"] == str(12 * sandbox.CPU_SECONDS)


def test_cores_are_counted():
    assert sandbox.script_cores() >= 1


def test_a_limit_stop_names_both_caps(monkeypatch, tmp_path):
    monkeypatch.delenv("AICAD_SCRIPT_CPU_SECONDS", raising=False)
    monkeypatch.delenv("AICAD_SCRIPT_MEMORY_MB", raising=False)
    monkeypatch.setattr(sandbox, "script_cores", lambda: 24)
    job = tmp_path / "job"
    job.mkdir()
    try:
        sandbox._payload_from_result(job, 1, "", "", memory_killed=True)
    except ScriptError as exc:
        text = str(exc)
    else:
        raise AssertionError("a limit stop was not reported")
    assert "hit the memory or CPU limit" in text
    assert "4096 MiB of memory" in text
    assert "1440 s of CPU time (60 s for each of 24 cores)" in text
