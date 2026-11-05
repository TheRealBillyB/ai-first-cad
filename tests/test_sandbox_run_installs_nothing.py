"""A Windows run needs no administrator rights: it reads the block and proves it works.

Nothing here touches this computer's firewall. The real runs are in
tests/test_sandbox_setup_real.py, on a disposable CI runner.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aicad import sandbox, sandbox_windows as win


class _Stop(OSError):
    """Stands in for everything after the step a test is looking at."""


def _spawn(tmp_path: Path, **kwargs):
    return win.spawn(
        ["python"],
        cwd=str(tmp_path),
        env={},
        read_paths=[tmp_path],
        workspace=tmp_path,
        cpu_seconds=1,
        memory_bytes=1,
        timeout_seconds=1,
        stdout_path=tmp_path / "out",
        stderr_path=tmp_path / "err",
        **kwargs,
    )


@pytest.fixture
def steps(monkeypatch):
    """The order spawn takes its steps in. Starting a container ends the test."""

    seen: list = []
    monkeypatch.setattr(win, "_bind", lambda: None)

    def mode(_paths):
        seen.append("mode")
        return "appcontainer"

    def container(profile):
        seen.append(("container", profile))
        raise _Stop("stop here")

    monkeypatch.setattr(win, "ensure_mode", mode)
    monkeypatch.setattr(win, "_appcontainer_sid", container)
    return seen


def test_the_run_path_has_no_way_to_change_the_firewall():
    source = Path(win.__file__).read_text(encoding="utf-8")
    for call in ("New-NetFirewallRule", "Remove-NetFirewallRule", "FwpmFilterAdd0", "FwpmFilterDeleteByKey0", "powershell"):
        assert call not in source
    for name in ("install_script", "remove_script", "_install", "_remove", "run_setup"):
        assert f"setup.{name}" not in source


@pytest.mark.parametrize(("found", "needed"), [(0, True), (7, True), (8, False)])
def test_every_filter_has_to_be_readable(monkeypatch, found, needed):
    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win.setup, "filters_present", lambda: found)
    message = win.network_block_error()
    assert (message is not None) is needed
    if needed:
        assert "aicad sandbox-setup" in message
        assert "administrator" in message
        assert "Shape, sketch, gear, and catalog parts still work." in message


def test_a_failed_read_is_not_a_block(monkeypatch):
    def refused():
        raise OSError("The filtering engine did not open.")

    monkeypatch.setattr(win.sys, "platform", "win32")
    monkeypatch.setattr(win.setup, "filters_present", refused)
    assert "aicad sandbox-setup" in win.network_block_error()


def test_a_network_off_run_without_the_block_starts_nothing(tmp_path, monkeypatch, steps):
    monkeypatch.setattr(win, "network_block_error", lambda: "needs setup")
    with pytest.raises(OSError, match="needs setup"):
        _spawn(tmp_path, network=False, child_checks_network=True)
    assert steps == []


def test_a_script_that_checks_for_itself_starts_in_the_blocked_container(tmp_path, monkeypatch, steps):
    monkeypatch.setattr(win, "network_block_error", lambda: None)
    monkeypatch.setattr(win, "_probe_mode", lambda *_args: pytest.fail("the script checks for itself"))
    with pytest.raises(_Stop):
        _spawn(tmp_path, network=False, child_checks_network=True)
    assert steps == ["mode", ("container", "aicadscript")]


def test_a_helper_is_proven_before_it_starts(tmp_path, monkeypatch, steps):
    monkeypatch.setattr(win, "network_block_error", lambda: None)
    sources = []

    def probe(mode, _paths, source):
        steps.append("proof")
        sources.append(source)

    monkeypatch.setattr(win, "_probe_mode", probe)
    with pytest.raises(_Stop):
        _spawn(tmp_path, network=False)
    assert steps == ["mode", "proof", ("container", "aicadscript")]
    assert "windows_network_is_off" in sources[0]
    compile(sources[0], "probe.py", "exec")


def test_a_helper_does_not_start_when_the_network_is_not_refused(tmp_path, monkeypatch, steps):
    monkeypatch.setattr(win, "network_block_error", lambda: None)
    monkeypatch.setattr(win, "_probe_mode", lambda *_args: "udp-sendto=sent")
    with pytest.raises(OSError, match="udp-sendto=sent") as caught:
        _spawn(tmp_path, network=False)
    assert "not refused" in str(caught.value)
    assert ("container", "aicadscript") not in steps


def test_a_network_on_run_uses_its_own_container_and_leaves_the_block_alone(tmp_path, monkeypatch, steps):
    monkeypatch.setattr(win, "network_block_error", lambda: pytest.fail("this run does not depend on the block"))
    monkeypatch.setattr(win, "_probe_mode", lambda *_args: pytest.fail("this run is allowed the network"))
    with pytest.raises(_Stop):
        _spawn(tmp_path, network=True)
    assert steps == ["mode", ("container", "aicadscriptnet")]


def test_each_container_gets_its_own_folder_grant(tmp_path, monkeypatch):
    grants = []
    monkeypatch.setattr(win, "_granted_read", set())
    monkeypatch.setattr(win, "_grant_tree", lambda path, sid, rights: grants.append(sid))
    monkeypatch.setattr(win, "_grant_traverse", lambda *_args: None)
    monkeypatch.setattr(win, "_marker_path", lambda: tmp_path / "marker")
    win.grant_read_paths([tmp_path], "S-1-15-2-1")
    win.grant_read_paths([tmp_path], "S-1-15-2-1")
    win.grant_read_paths([tmp_path], "S-1-15-2-2")
    assert grants == ["S-1-15-2-1", "S-1-15-2-2"]


def test_a_running_script_in_one_container_does_not_hold_the_other(tmp_path, monkeypatch):
    monkeypatch.setattr(win, "_profile_lock_path", lambda: tmp_path / "lock")
    monkeypatch.setattr(win, "_pid_alive", lambda _pid: True)
    win._change_profile_use(1, "aicadscript")
    assert win._profile_is_in_use("aicadscript") is True
    assert win._profile_is_in_use("aicadscriptnet") is False


def test_setup_in_another_window_is_seen_without_a_restart(monkeypatch):
    answers = iter(["needs setup", None])
    monkeypatch.setattr(win, "network_block_error", lambda: next(answers))
    monkeypatch.setattr(win, "_probe_done", True)
    monkeypatch.setattr(win, "_probe_result", None)
    monkeypatch.setattr(win.ctypes, "sizeof", lambda _kind: 8)
    assert win.availability_error([Path("python")]) == "needs setup"
    assert win.availability_error([Path("python")]) is None


def test_the_fallback_to_a_normal_container_is_visible(monkeypatch, capsys):
    monkeypatch.setattr(win, "_mode", None)
    monkeypatch.setattr(win, "_mode_error", None)
    monkeypatch.setattr(win, "_lpac_fallback_reason", None)
    assert win.mode_report() == {}
    answers = iter(["exited 0xC0000022", None])
    monkeypatch.setattr(win, "_probe_mode", lambda _mode, _paths: next(answers))
    assert win.ensure_mode([]) == "appcontainer"
    assert win.mode_report() == {"mode": "appcontainer", "stricter_mode_failed": "exited 0xC0000022"}
    captured = capsys.readouterr()
    assert "0xC0000022" in captured.err
    assert captured.out == ""


def test_the_stricter_container_reports_no_fallback(monkeypatch):
    monkeypatch.setattr(win, "_mode", None)
    monkeypatch.setattr(win, "_mode_error", None)
    monkeypatch.setattr(win, "_lpac_fallback_reason", None)
    monkeypatch.setattr(win, "_probe_mode", lambda _mode, _paths: None)
    assert win.ensure_mode([]) == "lpac"
    assert win.mode_report() == {"mode": "lpac"}


def test_the_script_log_records_which_container_ran(tmp_path):
    sandbox._append_log(tmp_path, "print(1)", True, None, mechanism="appcontainer+job")
    sandbox._append_log(tmp_path, "print(2)", True, None)
    rows = [json.loads(line) for line in sandbox.script_log_path(tmp_path).read_text(encoding="utf-8").splitlines()]
    assert rows[0]["mechanism"] == "appcontainer+job"
    assert "mechanism" not in rows[1]


def test_a_refused_network_names_the_setup_state():
    text = sandbox.windows_network_failure_text(
        attempts=(("tcp", "sent"),),
        package="S-1-15-2-1",
        capabilities=(),
    )
    assert text.startswith("Windows did not turn the network off, so the script was not run.")
    assert "The block is set up, but the network was not refused." in text
