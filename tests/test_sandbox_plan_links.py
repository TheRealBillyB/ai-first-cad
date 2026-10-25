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
from aicad.plan import PlanStore, plan_files

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
