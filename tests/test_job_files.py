"""A script job file is not opened when that name is a link."""

from __future__ import annotations

import json

import pytest

from aicad.sandbox_run import _JOB_MAX_BYTES, _read_job_text, _read_script, _write, main


def test_a_job_file_is_read_and_the_result_is_written(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.json").write_text('{"kind": "part"}\n', encoding="utf-8")
    (job / "script.py").write_text("part = Box(1, 1, 1)\n", encoding="utf-8")
    assert json.loads(_read_job_text(job / "job.json", _JOB_MAX_BYTES)) == {"kind": "part"}
    assert "Box" in _read_script(job)
    _write(job, {"ok": True, "probe": {}})
    assert json.loads((job / "result.json").read_text(encoding="utf-8"))["ok"] is True


def test_a_job_link_is_not_followed(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"kind": "part", "network": true}\n', encoding="utf-8")
    job = tmp_path / "job"
    job.mkdir()
    link = job / "job.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(OSError):
        _read_job_text(link, _JOB_MAX_BYTES)
    assert main([str(job)]) == 0
    assert outside.read_text(encoding="utf-8").startswith("{")
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "could not be read" in result["error"]


def test_a_script_link_is_not_read(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("print('no')\n", encoding="utf-8")
    job = tmp_path / "job"
    job.mkdir()
    link = job / "script.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(OSError):
        _read_script(job)
    assert outside.read_text(encoding="utf-8") == "print('no')\n"


def test_a_result_link_is_not_written(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    job = tmp_path / "job"
    job.mkdir()
    link = job / "result.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available")
    _write(job, {"ok": True})
    assert outside.read_text(encoding="utf-8") == "secret"
    assert not link.is_symlink()
    assert json.loads(link.read_text(encoding="utf-8"))["ok"] is True


def test_an_oversized_job_file_is_not_read(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    path = job / "job.json"
    path.write_bytes(b"{" + b" " * _JOB_MAX_BYTES)
    with pytest.raises(OSError):
        _read_job_text(path, _JOB_MAX_BYTES)
    assert path.stat().st_size > _JOB_MAX_BYTES
