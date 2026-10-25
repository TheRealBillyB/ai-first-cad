"""Appending to the script log does not read the whole file."""

from aicad.sandbox import _MAX_SCRIPT_LOG_BYTES, _append_log, script_log_path


def test_appending_a_script_log_keeps_only_the_newest_part(tmp_path, monkeypatch):
    monkeypatch.setattr("aicad.sandbox._MAX_SCRIPT_LOG_BYTES", 64)
    project = tmp_path / "project"
    log = script_log_path(project)
    log.parent.mkdir(parents=True)
    log.write_bytes(b"A" * 10000)
    _append_log(project, "print(1)\n", True, None)
    stored = log.read_bytes()
    assert len(stored) < 1000
    assert b"print(1)" in stored
    assert _MAX_SCRIPT_LOG_BYTES > 64
