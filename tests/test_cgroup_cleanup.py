"""A process cgroup that stays busy is removed on a later stop."""

import errno
from pathlib import Path

from aicad import sandbox as sandbox_mod


def test_a_busy_cgroup_directory_is_removed_on_a_later_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_mod.time, "sleep", lambda _seconds: None)
    path = tmp_path / "aicad-busy"
    path.mkdir()
    (path / "cgroup.procs").write_text("", encoding="utf-8")
    real_rmdir = Path.rmdir
    calls = {"n": 0}

    def flaky(self: Path) -> None:
        if Path(self) == path:
            calls["n"] += 1
            if calls["n"] <= 25:
                raise OSError(errno.EBUSY, "Device or resource busy")
            for child in Path(self).iterdir():
                child.unlink()
        return real_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", flaky)
    with sandbox_mod._leftover_cgroup_lock:
        sandbox_mod._leftover_cgroups.clear()
    sandbox_mod._release_pids_cgroup(path)
    assert path.is_dir()
    sandbox_mod._release_pids_cgroup(None)
    assert not path.exists()
    with sandbox_mod._leftover_cgroup_lock:
        assert str(path) not in sandbox_mod._leftover_cgroups
