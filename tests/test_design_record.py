"""The design record is written to the file the parent opened."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from aicad.file_access import open_inplace


def test_open_inplace_does_not_follow_a_link(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"kept")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(OSError):
        open_inplace(link)
    assert target.read_bytes() == b"kept"
    record = tmp_path / "record.json"
    record.write_bytes(b"old")
    descriptor = open_inplace(record)
    try:
        os.write(descriptor, b"new")
    finally:
        os.close(descriptor)
    assert record.read_bytes() == b"new"


def test_model_main_writes_the_open_file_not_the_path(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_WORKSPACE", str(tmp_path))
    job = tmp_path / "job"
    calls = job / "calls"
    calls.mkdir(parents=True)
    (calls / "STOP").write_text("", encoding="utf-8")
    descriptor, name = tempfile.mkstemp(prefix="aicad-record-", suffix=".json")
    duplicate = os.dup(descriptor)
    try:
        swapped = tmp_path / "swapped"
        swapped.write_bytes(b"SWAPPED")
        os.replace(swapped, name)
        if sys.platform == "win32":
            import msvcrt

            token = msvcrt.get_osfhandle(descriptor)
        else:
            token = descriptor
        from aicad.design_calls import model_main

        assert model_main(job, Path(name), token) == 0
        os.lseek(duplicate, 0, os.SEEK_SET)
        body = os.read(duplicate, 4096)
        assert b'"checks"' in body
        assert Path(name).read_bytes() == b"SWAPPED"
    finally:
        os.close(duplicate)
        Path(name).unlink(missing_ok=True)


def test_the_parent_passes_the_open_record(tmp_path, monkeypatch):
    from aicad.sandbox import _DesignRecord

    captured: dict = {}

    class Fake:
        def __init__(self, command, **kwargs):
            captured["command"] = list(command)
            captured["pass_fds"] = kwargs.get("pass_fds")
            captured["startupinfo"] = kwargs.get("startupinfo")

        def poll(self):
            return 0

    monkeypatch.setattr("aicad.sandbox.subprocess.Popen", Fake)
    job = tmp_path / "job"
    job.mkdir()
    record = _DesignRecord(tmp_path, job)
    record.start()
    try:
        command = captured["command"]
        assert command[command.index("--model") + 1] == str(job)
        token = int(command[-1])
        if sys.platform == "win32":
            handles = captured["startupinfo"].lpAttributeList["handle_list"]
            assert token in handles
        else:
            assert token in captured["pass_fds"]
    finally:
        record.close()


def test_model_dispatch_accepts_an_open_file(monkeypatch):
    from aicad import sandbox_run

    seen: dict = {}

    def fake(job, record, record_fd=None):
        del job, record
        seen["fd"] = record_fd
        return 0

    monkeypatch.setattr("aicad.design_calls.model_main", fake)
    assert sandbox_run.main(["--model", "job", "record"]) == 0
    assert seen["fd"] is None
    assert sandbox_run.main(["--model", "job", "record", "7"]) == 0
    assert seen["fd"] == 7
    assert sandbox_run.main(["--model", "job", "record", "nope"]) == 2
