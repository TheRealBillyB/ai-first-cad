"""A container probe that cannot start, or does not finish, comes back promptly with a clear reason.

A child that was refused a DLL used to show "Application Error 0xc0000022" on
the person's desktop and stay alive. The probe then waited its full 60 seconds
and read the exit code of a process that was still running: 259, STILL_ACTIVE.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from aicad import sandbox_windows as win

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer")

_BASE_PYTHON = str(Path(sys.base_prefix).resolve() / "python.exe")


def _probe(monkeypatch, mode, command):
    win._bind()
    monkeypatch.setattr(win, "_probe_command", lambda workspace, source=None: command)
    began = time.monotonic()
    result = win._probe_mode(mode, [Path(sys.base_prefix).resolve()], None)
    return result, time.monotonic() - began


def test_a_launched_child_inherits_no_failure_dialogs():
    win._bind()
    kernel32 = win._kernel32()
    win._quiet_load_failures(kernel32)
    assert kernel32.GetErrorMode() & win._QUIET_FAILURES == win._QUIET_FAILURES


def test_a_probe_that_does_not_finish_is_stopped_and_says_so(monkeypatch):
    monkeypatch.setattr(win, "_PROBE_WAIT_MS", 3_000)
    result, seconds = _probe(monkeypatch, "appcontainer", [_BASE_PYTHON, "-I", "-S", "-c", "import time; time.sleep(120)"])
    assert result == "the probe did not finish in 3 seconds, so it was stopped"
    assert seconds < 30
    assert "259" not in result


def test_the_stricter_container_answers_promptly_with_a_reason(monkeypatch):
    """On GitHub's runners and some desktops, Python is refused while it starts in LPAC.

    That has to be a prompt, readable answer, never a dialog and a 60-second wait.
    """
    result, seconds = _probe(monkeypatch, "lpac", [_BASE_PYTHON, "-I", "-S", "-c", "print('sandbox-ok')"])
    assert seconds < 30
    assert result is None or ("259" not in result and "STILL_ACTIVE" not in result), result


@pytest.mark.parametrize(
    ("code", "words"),
    [
        (0xC0000022, "access denied while it started (0xC0000022)"),
        (0xC0000135, "a DLL it needs was not found (0xC0000135)"),
        (0xC0000142, "a DLL failed to initialize (0xC0000142)"),
        (0xC0000044, "it reached its CPU limit (0xC0000044)"),
        (3, "the probe exited with code 3"),
    ],
)
def test_a_known_start_failure_is_named(code, words):
    assert words in win._exit_reason(code)


def test_every_launch_sets_the_error_mode_before_it_starts_a_child():
    """_launch owns it: it uses the real kernel32, so a test's stand-in for CreateProcess needs none."""
    import inspect

    launch = inspect.getsource(win._launch)
    assert launch.index("_quiet_load_failures(kernel32)") < launch.index("_start_suspended(")
    assert "_quiet_load_failures" not in inspect.getsource(win._start_suspended)
