"""Shared files and meshes stop at a fixed size instead of growing without a limit.

The canary is a marker that must not be written outside the project. An
oversized file is sparse: the test checks the size and does not fill the disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.app import _MAX_SERVED_BYTES, make_server
from aicad.document import Document
from aicad.errors import GeometryError, ScriptError
from aicad.kernel import MAX_STEP_BYTES, import_step_file, read_step_bytes, tessellate, viewport_mesh
from aicad.sandbox import DISK_BYTES, FILE_BYTES, _DiskWatch, _snapshot_files, disk_bytes, file_bytes, run_part_script, sandbox_supported


def _sparse(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.seek(size - 1)
        handle.write(b"x")


def test_an_oversized_step_file_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    project = tmp_path / "project"
    project.mkdir()
    step = project / "huge.step"
    _sparse(step, MAX_STEP_BYTES + 1)
    before = step.stat().st_size
    with pytest.raises(GeometryError, match="too large"):
        read_step_bytes(step)
    with pytest.raises(GeometryError, match="too large"):
        import_step_file(step)
    document = Document(project)
    with pytest.raises(GeometryError, match="too large"):
        document.import_step("huge", step)
    assert step.stat().st_size == before
    assert not (project / "imports" / "huge.step").exists()
    assert not canary.exists()
    assert list(project.rglob("SANDBOX_CANARY.txt")) == []


def test_a_small_step_still_imports(tmp_path):
    from aicad.kernel import export_step_file, make_from_ops

    project = tmp_path / "project"
    project.mkdir()
    source = project / "box.step"
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": [4, 4, 4]}]), source)
    document = Document(project)
    info = document.import_step("box", source)
    assert info.name == "box"
    assert (project / "imports" / "box.step").stat().st_size < MAX_STEP_BYTES


def test_an_oversized_workspace_file_is_refused(tmp_path, monkeypatch):
    import json
    import threading
    from http.client import HTTPConnection

    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    project = tmp_path / "project"
    project.mkdir()
    picture = project / "huge.png"
    _sparse(picture, _MAX_SERVED_BYTES + 1)
    server = make_server(project, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        connection = HTTPConnection(host, port, timeout=30)
        connection.request(
            "GET",
            "/api/file?path=huge.png",
            headers={
                "Origin": f"http://127.0.0.1:{port}",
                "X-Aicad-Token": server.page_token,
            },
        )
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    assert status == 400
    body = json.loads(payload.decode("utf-8"))
    assert "too large" in body["error"]
    assert len(payload) < 4096
    assert picture.stat().st_size == _MAX_SERVED_BYTES + 1


def test_a_mesh_stops_at_the_triangle_cap(monkeypatch):
    from aicad import kernel
    from aicad.kernel import make_from_ops

    solid = make_from_ops([{"op": "add", "shape": "box", "size": [8, 6, 4]}])
    mesh = tessellate(solid)
    assert 0 < len(mesh.triangles) <= kernel._MAX_TRIANGLES
    view = viewport_mesh(solid)
    assert 0 < len(view["triangle_face"]) <= kernel._MAX_TRIANGLES
    monkeypatch.setattr(kernel, "_MAX_TRIANGLES", 1)
    with pytest.raises(GeometryError, match="too many triangles"):
        tessellate(solid)
    with pytest.raises(GeometryError, match="too many triangles"):
        viewport_mesh(solid)


def test_file_bytes_cannot_be_raised(monkeypatch):
    assert file_bytes() == FILE_BYTES
    monkeypatch.setenv("AICAD_SCRIPT_FILE_BYTES", str(FILE_BYTES * 4))
    assert file_bytes() == FILE_BYTES
    monkeypatch.setenv("AICAD_SCRIPT_FILE_BYTES", "2048")
    assert file_bytes() == 2048


def test_disk_bytes_cannot_be_raised(monkeypatch):
    assert disk_bytes() == DISK_BYTES
    monkeypatch.setenv("AICAD_SCRIPT_DISK_BYTES", str(DISK_BYTES * 4))
    assert disk_bytes() == DISK_BYTES
    monkeypatch.setenv("AICAD_SCRIPT_DISK_BYTES", "4096")
    assert disk_bytes() == 4096


def test_extra_files_are_removed_when_the_disk_quota_is_passed(tmp_path):
    before = _snapshot_files(tmp_path)
    (tmp_path / "a.bin").write_bytes(b"a" * 1500)
    (tmp_path / "b.bin").write_bytes(b"b" * 1500)
    message = _DiskWatch(tmp_path, before, file_cap=2000, disk_cap=2000).settle()
    assert message is not None
    assert "wrote more than 2000 bytes" in message
    total = 0
    for name in ("a.bin", "b.bin"):
        path = tmp_path / name
        if path.exists():
            total += path.stat().st_size
    assert total <= 2000


def test_a_script_stops_when_the_file_cap_is_hit(tmp_path, monkeypatch):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_SCRIPT_FILE_BYTES", "65536")
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    small = (
        "from pathlib import Path\n"
        "Path('ok.txt').write_text('ok', encoding='utf-8')\n"
        "part = Box(1, 1, 1)\n"
    )
    run_part_script(small, project)
    assert (project / "ok.txt").read_text(encoding="utf-8") == "ok"
    script = (
        "from pathlib import Path\n"
        "Path('big.bin').write_bytes(b'x' * 100000)\n"
        "part = Box(1, 1, 1)\n"
    )
    with pytest.raises(ScriptError):
        run_part_script(script, project)
    written = project / "big.bin"
    assert not written.exists() or written.stat().st_size <= 65536
    assert not canary.exists()


def test_a_script_stops_when_two_files_pass_the_disk_quota(tmp_path, monkeypatch):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_SCRIPT_DISK_BYTES", "200000")
    monkeypatch.setenv("AICAD_SCRIPT_FILE_BYTES", "200000")
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    script = (
        "from pathlib import Path\n"
        "Path('a.bin').write_bytes(b'a' * 150000)\n"
        "Path('b.bin').write_bytes(b'b' * 150000)\n"
        "part = Box(1, 1, 1)\n"
    )
    with pytest.raises(ScriptError, match="wrote more than"):
        run_part_script(script, project)
    total = 0
    for name in ("a.bin", "b.bin"):
        path = project / name
        if path.exists():
            total += path.stat().st_size
    assert total <= 200000


def test_the_windows_job_counts_bytes_written():
    import ctypes

    from aicad.sandbox_windows import _IoCounters, _JobBasicAccounting, _JobBasicAndIo

    assert _JobBasicAndIo.IoInfo.offset == ctypes.sizeof(_JobBasicAccounting)
    assert ctypes.sizeof(_JobBasicAndIo().IoInfo) == ctypes.sizeof(_IoCounters)
    names = [name for name, _kind in _IoCounters._fields_]
    assert names[4] == "WriteTransferCount"


def test_the_macos_file_helper_uses_the_script_cap():
    import inspect

    from aicad import sandbox_macos

    source = inspect.getsource(sandbox_macos.apply_limits)
    assert "file_bytes" in source
    assert "8 * 1024 * 1024 * 1024" not in source


def test_the_first_io_sample_counts_from_zero(tmp_path, monkeypatch):
    monkeypatch.setattr("aicad.sandbox._process_write_bytes", lambda pid: 5000)
    disk = _DiskWatch(tmp_path, {}, file_cap=10**9, disk_cap=1000)
    disk.note_io({1})
    assert disk.io_peak == 5000
    assert disk.hit.is_set()


def test_a_baseline_is_not_counted_as_a_script_write(tmp_path, monkeypatch):
    values = {1: 10}
    monkeypatch.setattr("aicad.sandbox._process_write_bytes", lambda pid: values[pid])
    disk = _DiskWatch(tmp_path, {}, file_cap=10**9, disk_cap=1000)
    disk.baseline(1)
    values[1] = 5000
    disk.note_io({1})
    assert disk.io_peak == 4990
    assert disk.hit.is_set()


def test_linux_write_accounting_uses_logical_bytes(monkeypatch):
    import sys

    if not sys.platform.startswith("linux"):
        pytest.skip("Linux reports logical writes in /proc")
    from aicad import sandbox

    def fake_read(self, encoding=None, errors=None):
        del self, encoding, errors
        return "rchar: 9\nwchar: 1234\nread_bytes: 0\nwrite_bytes: 50\n"

    monkeypatch.setattr(Path, "read_text", fake_read)
    assert sandbox._process_write_bytes(1) == 1234


def test_macos_write_accounting_asks_for_logical_writes():
    from aicad import sandbox_macos

    assert sandbox_macos._RUSAGE_INFO_V4 == 4
    assert sandbox_macos._RUSAGE_INFO_V2 == 2
    names = [name for name, _kind in sandbox_macos._RusageInfoV4._fields_]
    assert "ri_logical_writes" in names
    assert names.index("ri_logical_writes") > names.index("ri_diskio_byteswritten")


def test_job_io_is_counted_from_the_baseline(tmp_path, monkeypatch):
    from aicad import sandbox_windows

    seen = {"calls": 0}

    def fake(_job):
        seen["calls"] += 1
        return 1000 + seen["calls"] * 5000

    monkeypatch.setattr(sandbox_windows, "_job_bytes_written", fake)
    disk = _DiskWatch(tmp_path, {}, file_cap=10**9, disk_cap=1000)
    sandbox_windows._account_job_io(disk, object(), 1000)
    assert disk.io_peak == 5000
    assert disk.hit.is_set()
    quiet = _DiskWatch(tmp_path, {}, file_cap=10**9, disk_cap=10**9)
    sandbox_windows._account_job_io(quiet, object(), None)
    assert quiet.io_peak == 1000 + seen["calls"] * 5000
    assert not quiet.hit.is_set()


def test_a_removed_file_still_counts_toward_the_disk_quota(tmp_path, monkeypatch):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_SCRIPT_DISK_BYTES", "200000")
    monkeypatch.setenv("AICAD_SCRIPT_FILE_BYTES", str(512 * 1024 * 1024))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    script = (
        "from pathlib import Path\n"
        "path = Path('gone.bin')\n"
        "path.write_bytes(b'x' * 500000)\n"
        "path.unlink()\n"
        "n = 0\n"
        "while n < 2000000:\n"
        "    n += 1\n"
        "part = Box(1, 1, 1)\n"
    )
    with pytest.raises(ScriptError, match="wrote more than"):
        run_part_script(script, project)
