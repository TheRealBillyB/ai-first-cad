"""A script stopped at a cap says which cap: CPU time or memory.

The message said "the memory or CPU limit" whichever it was. Windows knows:
user time at the cap is the CPU cap, a memory violation or peak is the memory
cap. Elsewhere SIGXCPU is the CPU cap and the parent's memory watch is the
memory cap. The wall clock already had its own message.
"""

from __future__ import annotations

import signal

import pytest

from aicad import sandbox
from aicad import sandbox_windows as win
from aicad.errors import ScriptError


def _stop_text(tmp_path, code: int, memory_killed) -> str:
    job = tmp_path / "job"
    job.mkdir()
    with pytest.raises(ScriptError) as caught:
        sandbox._payload_from_result(job, code, "", "", memory_killed=memory_killed)
    return str(caught.value)


@pytest.fixture(autouse=True)
def _caps(monkeypatch):
    monkeypatch.delenv("AICAD_SCRIPT_CPU_SECONDS", raising=False)
    monkeypatch.delenv("AICAD_SCRIPT_MEMORY_MB", raising=False)
    monkeypatch.setattr(sandbox, "script_cores", lambda: 24)


def test_windows_says_which_cap_stopped_the_job():
    def stop(**overrides):
        values = {"timed_out": False, "violated": False, "cpu_reached": False, "exit_code": 1, "memory_peak": False}
        values.update(overrides)
        return win._job_stop_was_a_limit(**values)

    assert stop(cpu_reached=True, exit_code=0) == "cpu"
    assert stop(cpu_reached=True, violated=True) == "cpu"
    assert stop(violated=True) == "memory"
    assert stop(memory_peak=True) == "memory"
    assert stop(memory_peak=True, exit_code=0) == ""
    assert stop(timed_out=True, cpu_reached=True) == ""
    assert stop() == ""


def test_a_cpu_stop_says_cpu_time(tmp_path):
    text = _stop_text(tmp_path, 1, "cpu")
    assert "used all of its CPU time (1440 s of CPU time (60 s for each of 24 cores))" in text
    assert "memory" not in text


def test_a_memory_stop_says_memory(tmp_path):
    text = _stop_text(tmp_path, 1, "memory")
    assert "hit the memory limit (4096 MiB of memory)" in text
    assert "CPU" not in text


def test_an_unknown_cap_still_names_both(tmp_path):
    text = _stop_text(tmp_path, 1, True)
    assert "hit the memory or CPU limit (4096 MiB of memory, 1440 s of CPU time" in text


@pytest.mark.skipif(not hasattr(signal, "SIGXCPU"), reason="no SIGXCPU on this system")
def test_sigxcpu_is_the_cpu_cap(tmp_path):
    text = _stop_text(tmp_path, -signal.SIGXCPU, False)
    assert "used all of its CPU time" in text
