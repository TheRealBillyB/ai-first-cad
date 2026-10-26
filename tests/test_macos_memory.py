"""Reading a process's memory use does not wait forever."""

from __future__ import annotations

import subprocess

from aicad import sandbox_macos as mac


def test_a_stuck_ps_does_not_hold_the_memory_read(monkeypatch):
    def stuck(*_args, **kwargs):
        assert kwargs.get("timeout") == 2
        raise subprocess.TimeoutExpired(cmd="ps", timeout=2)

    monkeypatch.setattr(mac.subprocess, "run", stuck)
    assert mac._resident_via_ps(1) is None

    def missing(*_args, **_kwargs):
        raise OSError("ps missing")

    monkeypatch.setattr(mac.subprocess, "run", missing)
    assert mac._resident_via_ps(1) is None


def test_ps_memory_is_read_in_bytes(monkeypatch):
    class Completed:
        stdout = "  12\n"

    def ok(command, **kwargs):
        assert command[:3] == ["ps", "-o", "rss="]
        assert kwargs.get("timeout") == 2
        return Completed()

    monkeypatch.setattr(mac.subprocess, "run", ok)
    assert mac._resident_via_ps(4) == 12 * 1024
