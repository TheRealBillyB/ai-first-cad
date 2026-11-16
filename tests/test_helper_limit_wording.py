"""A CalculiX stop at a limit names the limits that helper actually had.

Only the Windows helper runs under memory and CPU caps of its own (a job
object), and its CPU cap is the timeout for each thread it was told to use.
On Linux and macOS the helper has neither cap, so the limit is the
computer's free memory.
"""

from aicad import fea


def test_a_capped_helper_names_its_memory_and_its_cpu_time(monkeypatch):
    monkeypatch.setattr(fea, "_helper_has_caps", lambda: True)
    monkeypatch.setattr("aicad.sandbox.memory_bytes", lambda: 4096 * 1024 * 1024)
    text = fea.helper_limit_stop(180)
    assert text == "was stopped at the memory or CPU limit (4096 MiB of memory, 180 s of CPU time)"


def test_a_capped_helper_with_threads_counts_each_threads_time(monkeypatch):
    monkeypatch.setattr(fea, "_helper_has_caps", lambda: True)
    monkeypatch.setattr("aicad.sandbox.memory_bytes", lambda: 4096 * 1024 * 1024)
    text = fea.helper_limit_stop(180, threads=4)
    assert "720 s of CPU time (180 s for each of 4 threads)" in text


def test_a_helper_without_caps_names_the_computers_memory(monkeypatch):
    monkeypatch.setattr(fea, "_helper_has_caps", lambda: False)
    text = fea.helper_limit_stop(180, threads=4)
    assert "CPU time" not in text and "MiB" not in text
    assert text.startswith("ran out of memory")
    assert "the computer's free memory" in text


def test_only_windows_has_caps(monkeypatch):
    monkeypatch.setattr(fea.sys, "platform", "linux")
    assert fea._helper_has_caps() is False
    monkeypatch.setattr(fea.sys, "platform", "win32")
    assert fea._helper_has_caps() is True
