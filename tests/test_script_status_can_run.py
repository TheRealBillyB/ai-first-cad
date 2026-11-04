"""Allowed is not the same as able to run."""

from aicad.workspace import Workspace


def test_status_says_when_this_computer_cannot_confine_a_script(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    reason = f"No sandbox for {tmp_path.resolve()} on this computer."
    monkeypatch.setattr("aicad.sandbox.sandbox_supported", lambda: (False, reason))
    status = workspace.execute("design_script_status", {})
    assert status["enabled"] is False
    assert status["can_run"] is False
    assert status["cannot_run"] == "No sandbox for . on this computer."


def test_status_is_quiet_when_a_script_can_run(tmp_path, monkeypatch):
    monkeypatch.setattr("aicad.sandbox.sandbox_supported", lambda: (True, ""))
    status = Workspace(tmp_path).execute("design_script_status", {})
    assert status["can_run"] is True
    assert "cannot_run" not in status


def test_the_state_read_does_not_run_the_sandbox_probe(tmp_path, monkeypatch):
    def probed():
        raise AssertionError("the page read started the sandbox probe")

    monkeypatch.setattr("aicad.sandbox.sandbox_supported", probed)
    assert "can_run" not in Workspace(tmp_path).state()["design_scripts"]
