"""A script job file is not opened when that name is a link."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aicad.sandbox_run import _JOB_MAX_BYTES, _read_job_text, _read_script, _write


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
    # The child ends its own process on Windows. A separate process keeps this one.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\nfrom aicad.sandbox_run import main\nraise SystemExit(main([sys.argv[1]]))\n",
            str(job),
        ],
        check=False,
    )
    assert completed.returncode == 0
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


def test_a_replaced_write_text_is_what_gets_published(tmp_path, monkeypatch):
    job = tmp_path / "job"
    job.mkdir()
    real = Path.write_text

    def fake(self, data, *args, **kwargs):
        if self.name == "result.json":
            body = json.loads(data)
            body["step"] = "changed"
            data = json.dumps(body) + "\n"
        return real(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fake)
    _write(job, {"ok": True, "step": "original"})
    saved = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert saved["ok"] is True
    assert saved["step"] == "changed"
    assert list(job.iterdir()) == [job / "result.json"]


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


def test_the_script_result_is_opened_from_its_directory(tmp_path):
    import os
    import stat

    from aicad.file_access import open_write

    target = tmp_path / "target"
    target.write_bytes(b"CANARY_HIT\n")
    link = tmp_path / "result.json"
    try:
        link.symlink_to(target)
    except OSError:
        link = None
    if link is not None:
        with pytest.raises(OSError):
            open_write(tmp_path, link.name)
        assert target.read_bytes() == b"CANARY_HIT\n"
    fresh = tmp_path / "fresh.json"
    descriptor = open_write(tmp_path, fresh.name)
    try:
        os.write(descriptor, b"ok\n")
        if os.name != "nt":
            assert stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o600
    finally:
        os.close(descriptor)
    assert fresh.read_bytes() == b"ok\n"
    descriptor = open_write(tmp_path, fresh.name)
    try:
        os.write(descriptor, b"new\n")
    finally:
        os.close(descriptor)
    assert fresh.read_bytes() == b"new\n"
    other = tmp_path / "other"
    other.write_bytes(b"CANARY_HIT\n")
    alias = tmp_path / "alias"
    try:
        os.link(other, alias)
    except OSError:
        alias = None
    if alias is not None:
        with pytest.raises(OSError):
            open_write(tmp_path, alias.name)
        assert other.read_bytes() == b"CANARY_HIT\n"
    real = tmp_path / "real"
    real.mkdir()
    kept = real / "result.json"
    kept.write_bytes(b"CANARY_HIT\n")
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        if sys.platform != "win32":
            return
        import subprocess

        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked), str(real)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return
    with pytest.raises(OSError):
        open_write(linked, kept.name)
    assert kept.read_bytes() == b"CANARY_HIT\n"
