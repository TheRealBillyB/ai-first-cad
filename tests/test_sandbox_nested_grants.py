"""A folder inside one the sandbox already walked is not walked again.

The first sandbox run in a process granted the container read access to the
interpreter's environment, then walked its site-packages again: 9 s and 7 s
of icacls on this computer, the second changing nothing. No real folder's
access list is changed here: the grants are recorded, not made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad import sandbox_windows as win

_SID = "S-1-15-2-1"


@pytest.fixture
def walked(monkeypatch, tmp_path):
    record: list[tuple[str, str]] = []
    monkeypatch.setattr(win, "_granted_read", set())
    monkeypatch.setattr(win, "_granted_trees", set())
    monkeypatch.setattr(win, "_grant_tree", lambda path, sid, rights: record.append(("tree", Path(path).name)))
    monkeypatch.setattr(win, "_grant_file", lambda path, sid: record.append(("file", Path(path).name)))
    monkeypatch.setattr(win, "_grant_traverse", lambda *_args: None)
    monkeypatch.setattr(win, "_grant_links_on_the_way", lambda *_args: None)
    monkeypatch.setattr(win, "_marker_path", lambda: tmp_path / "marker")
    return record


def _folders(tmp_path: Path) -> dict[str, Path]:
    env = tmp_path / "env"
    site = env / "Lib" / "site-packages"
    site.mkdir(parents=True)
    (site / "module.py").write_text("", encoding="utf-8")
    sibling = tmp_path / "env2"
    sibling.mkdir()
    return {"env": env, "site": site, "module": site / "module.py", "sibling": sibling}


def test_a_folder_inside_one_walked_in_the_same_call_is_not_walked(tmp_path, walked):
    folders = _folders(tmp_path)
    # Inner first, as the sandbox lists them: the outer folder is still walked first.
    win.grant_read_paths([folders["site"], folders["module"], folders["env"]], _SID)
    assert walked == [("tree", "env")]
    for name in ("site", "module", "env"):
        assert (_SID, str(folders[name].resolve())) in win._granted_read


def test_a_folder_inside_one_walked_earlier_is_not_walked(tmp_path, walked):
    folders = _folders(tmp_path)
    win.grant_read_paths([folders["env"]], _SID)
    win.grant_read_paths([folders["site"]], _SID)
    assert walked == [("tree", "env")]


def test_another_container_and_a_sibling_with_a_longer_name_are_still_walked(tmp_path, walked):
    folders = _folders(tmp_path)
    win.grant_read_paths([folders["env"], folders["sibling"]], _SID)
    win.grant_read_paths([folders["site"]], "S-1-15-2-2")
    assert walked == [("tree", "env"), ("tree", "env2"), ("tree", "site-packages")]


def test_a_folder_walked_inside_does_not_cover_its_parent(tmp_path, walked):
    folders = _folders(tmp_path)
    win.grant_read_paths([folders["site"]], _SID)
    win.grant_read_paths([folders["env"]], _SID)
    assert walked == [("tree", "site-packages"), ("tree", "env")]
