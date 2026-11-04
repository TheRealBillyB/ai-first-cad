"""A refused firewall rule says why. The script still does not start."""

import subprocess

import pytest

from aicad import sandbox_windows


def _firewall_answers(monkeypatch, output):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout=output, stderr="")

    monkeypatch.setattr(sandbox_windows.subprocess, "run", fake_run)


def test_access_denied_names_the_administrator_step(monkeypatch):
    _firewall_answers(monkeypatch, "New-NetFirewallRule : Access is denied.\n+ CategoryInfo : PermissionDenied")
    with pytest.raises(OSError, match="administrator") as caught:
        sandbox_windows._run_firewall_script("exit 1")
    assert "CategoryInfo" not in str(caught.value)


def test_another_firewall_failure_keeps_its_detail(monkeypatch):
    _firewall_answers(monkeypatch, "the network block name is still registered")
    with pytest.raises(OSError, match="still registered"):
        sandbox_windows._run_firewall_script("exit 1")


@pytest.mark.parametrize(
    ("errors", "expected"),
    [
        (["same", "same"], "Both AppContainer modes failed (same)."),
        (["one", "two"], "A normal AppContainer failed (two)."),
    ],
)
def test_neither_mode_starts_and_the_script_is_not_run(monkeypatch, errors, expected):
    monkeypatch.setattr(sandbox_windows, "_mode", None)
    monkeypatch.setattr(sandbox_windows, "_mode_error", None)
    answers = iter(errors)
    monkeypatch.setattr(sandbox_windows, "_probe_mode", lambda mode, paths: next(answers))
    with pytest.raises(OSError) as caught:
        sandbox_windows.ensure_mode([])
    assert expected in str(caught.value)
    assert "The script was not run." in str(caught.value)
    assert sandbox_windows._mode is None
