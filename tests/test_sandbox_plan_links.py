"""A plan folder that is a link is not listed, and a plan file with another name is not copied.

The marker is CANARY_HIT in a file outside the project. Listing plans and
the undo copy must not return that text, and must not change the outside file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aicad.errors import PlanError
from aicad.plan import PlanStore, plan_files, restore_plan_files

_CANARY = "CANARY_HIT"


def _plan(name: str, title: str) -> dict:
    now = "2026-10-25T00:00:00Z"
    return {
        "format": 1,
        "units": "mm",
        "id": name,
        "title": title,
        "summary": "",
        "status": "draft",
        "current_revision": 1,
        "approved_revision": None,
        "approved_at": None,
        "created_at": now,
        "updated_at": now,
        "revisions": [
            {
                "version": 1,
                "created_at": now,
                "changed": "Started the plan.",
                "research": [],
                "assumptions": [],
                "questions": [],
                "dimensions": [],
                "sheets": [],
                "activity": [],
            }
        ],
    }


def _link_dir(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("this machine cannot create a junction")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory symlink")


def _dump(value) -> str:
    return json.dumps(value)


def test_a_plan_folder_that_is_a_link_is_not_listed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    canary.write_text("keep\n", encoding="utf-8")
    (outside / "shelf.json").write_text(
        json.dumps(_plan("shelf", _CANARY)),
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    _link_dir(project / "plans", outside)
    listed = PlanStore(project).summaries()
    copied = plan_files(project)
    assert _CANARY not in _dump(listed)
    assert _CANARY not in _dump(copied)
    assert copied == {}
    assert canary.read_text(encoding="utf-8") == "keep\n"
    with pytest.raises(PlanError):
        PlanStore(project).get("shelf")


def test_a_plan_file_with_another_name_is_not_opened(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    canary.write_text("keep\n", encoding="utf-8")
    secret = outside / "secret.json"
    secret.write_text(json.dumps(_plan("shelf", _CANARY)), encoding="utf-8")
    project = tmp_path / "project"
    plans = project / "plans"
    plans.mkdir(parents=True)
    try:
        os.link(secret, plans / "shelf.json")
    except OSError:
        pytest.skip("this machine cannot create a hard link")
    listed = PlanStore(project).summaries()
    copied = plan_files(project)
    assert _CANARY not in _dump(listed)
    assert _CANARY not in _dump(copied)
    assert "shelf.json" not in copied
    assert canary.read_text(encoding="utf-8") == "keep\n"
    assert secret.read_text(encoding="utf-8") == json.dumps(_plan("shelf", _CANARY))


def test_a_plan_file_that_is_a_link_is_not_opened(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    canary.write_text("keep\n", encoding="utf-8")
    secret = outside / "secret.json"
    secret.write_text(json.dumps(_plan("shelf", _CANARY)), encoding="utf-8")
    project = tmp_path / "project"
    plans = project / "plans"
    plans.mkdir(parents=True)
    try:
        (plans / "shelf.json").symlink_to(secret)
    except OSError:
        pytest.skip("this machine cannot create a file symlink")
    listed = PlanStore(project).summaries()
    copied = plan_files(project)
    assert _CANARY not in _dump(listed)
    assert _CANARY not in _dump(copied)
    assert canary.read_text(encoding="utf-8") == "keep\n"


def test_a_real_plan_is_still_listed_and_copied(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    made = PlanStore(project).start("shelf", title="Bracket")
    assert made["title"] == "Bracket"
    listed = PlanStore(project).summaries()
    assert [item["id"] for item in listed] == ["shelf"]
    assert listed[0]["title"] == "Bracket"
    copied = plan_files(project)
    assert "Bracket" in copied["shelf.json"]


def test_a_swapped_plan_is_not_read(tmp_path, monkeypatch):
    from aicad import paths

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    canary.write_text(_CANARY + "\n", encoding="utf-8")
    project = tmp_path / "project"
    plans = project / "plans"
    plans.mkdir(parents=True)
    (plans / "shelf.json").write_text(json.dumps(_plan("shelf", "Bracket")), encoding="utf-8")

    def swap(root, relative) -> None:
        if Path(relative).name != "shelf.json":
            return
        current = Path(root) / relative
        if current.is_symlink() or not current.is_file():
            return
        current.unlink()
        try:
            current.symlink_to(canary)
        except OSError:
            pytest.skip("this machine cannot create a symlink")

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    with pytest.raises(PlanError) as caught:
        PlanStore(project).get("shelf")
    assert _CANARY not in str(caught.value)
    copied = plan_files(project)
    listed = PlanStore(project).summaries()
    assert _CANARY not in _dump(copied)
    assert _CANARY not in _dump(listed)
    assert "shelf.json" not in copied
    assert canary.read_text(encoding="utf-8") == _CANARY + "\n"


def test_a_plan_past_the_cap_is_not_copied_and_undo_leaves_it(tmp_path):
    project = tmp_path / "project"
    plans = project / "plans"
    plans.mkdir(parents=True)
    huge = plans / "huge.json"
    huge.write_bytes(b"{" + b" " * 2_000_001)
    before = huge.read_bytes()
    (plans / "extra.json").write_text("{}\n", encoding="utf-8")
    copied = plan_files(project)
    assert "huge.json" not in copied
    assert copied["extra.json"] == "{}\n"
    listed = PlanStore(project).summaries()
    assert any("larger than a plan file" in item.get("error", "") for item in listed)
    assert _CANARY not in _dump(listed)
    restore_plan_files(project, {})
    assert huge.read_bytes() == before
    assert not (plans / "extra.json").exists()


def test_a_sheet_directory_link_is_not_entered(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "sheet.png"
    canary.write_bytes(b"\x89PNG\r\nCANARY_HIT\n")
    root = tmp_path / "project"
    version = root / "plans" / "shelf" / "v1"
    version.mkdir(parents=True)
    _link_dir(version / "joined", outside)
    loose = version / "loose.png"
    loose.write_bytes(b"loose")
    kept = version / "kept.png"
    kept.write_bytes(b"kept")
    empty = version / "empty"
    empty.mkdir()
    from aicad.plan import _drop_orphan_sheets

    _drop_orphan_sheets(
        root,
        {"id": "shelf", "revisions": [{"sheets": [{"path": "plans/shelf/v1/kept.png"}]}]},
    )
    assert canary.read_bytes().startswith(b"\x89PNG")
    assert (version / "joined").exists()
    assert not loose.exists()
    assert kept.read_bytes() == b"kept"
    assert not empty.exists()


def test_a_directory_treated_as_a_junction_keeps_its_sheets(tmp_path, monkeypatch):
    from aicad import paths
    from aicad.plan import _drop_orphan_sheets

    root = tmp_path / "project"
    version = root / "plans" / "shelf" / "v1"
    joined = version / "joined"
    joined.mkdir(parents=True)
    orphan = joined / "gone.png"
    orphan.write_bytes(b"\x89PNG\r\nCANARY_HIT\n")
    nested = joined / "nested"
    nested.mkdir()
    loose = version / "loose.png"
    loose.write_bytes(b"loose")
    real = paths.is_reparse

    def as_junction(candidate: Path) -> bool:
        if Path(candidate) == joined:
            return True
        return real(candidate)

    monkeypatch.setattr(paths, "is_reparse", as_junction)
    _drop_orphan_sheets(root, {"id": "shelf", "revisions": [{"sheets": []}]})
    assert orphan.read_bytes().startswith(b"\x89PNG")
    assert nested.is_dir()
    assert joined.is_dir()
    assert not loose.exists()


def test_a_missing_reparse_plan_folder_is_not_treated_as_absent(tmp_path, monkeypatch):
    from aicad import paths
    from aicad.plan import _drop_orphan_sheets

    root = tmp_path / "project"
    folder = root / "plans" / "shelf"
    seen = []
    real = paths.resolve_in_project

    def record(root_path, path, label, error):
        seen.append(Path(path))
        return real(root_path, path, label, error)

    monkeypatch.setattr(paths, "is_reparse", lambda candidate: Path(candidate) == folder)
    monkeypatch.setattr(paths, "resolve_in_project", record)
    _drop_orphan_sheets(root, {"id": "shelf", "revisions": []})
    assert seen == [folder]
