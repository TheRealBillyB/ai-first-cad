"""Plan & Sketch. Research, blueprint sheets, and a review stop before modeling.

A plan is data in the project folder: one JSON file and the PNG sheets it
describes. A plan folder that is a link is not listed. A plan file that is
a link, or that has another name, is not opened. Nothing in the file is
executed.

The person approves a plan. The agent does not. That approval is recorded on
this computer, outside the project. A plan file that says approved, including
one copied from someone else or edited by hand, does not open modeling.
Modeling and simulation that start from the plan stay off until the recorded
approval matches this revision. They then receive its dimensions and assumptions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aicad.errors import PlanError
from aicad.plan_sheet import FILLS, render_plan_sheet

_PLAN_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,40}$")
_ITEM_ID = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")
_STATUSES = {"draft", "in_review", "approved"}
_USER_ACTORS = {"user"}
_FORMAT = 1
_MAX_BYTES = 2_000_000
_MAX_TEXT = 4000
_MAX_SHEETS = 8
_MAX_BLOCKS = 80
_MAX_DIMS = 40

_ASSUMPTION_MARKS = {"open", "accepted", "rejected"}
_QUESTION_MARKS = {"open", "waived"}

_FOOTER = "Blocks and dimensions for review. This sheet is not a solid and not a shop drawing."


def status_phrase(status: str) -> str:
    if status == "in_review":
        return "in review"
    return status


def approval_refusal(plan: dict) -> str:
    version = plan["current_revision"]
    return (
        f"Only you can approve {plan['title']!r} (revision v{version}). "
        "The agent cannot approve a plan. "
        "Open the Plan panel in the workspace and approve it there, "
        "or run approve-plan yourself. Nothing was approved."
    )


def answer_refusal() -> str:
    return (
        "Only you can answer this question. The agent cannot answer for you. "
        "Type the answer in the Plan panel, or run answer-plan-question yourself."
    )


def mark_refusal() -> str:
    return (
        "Only you can mark this item. The agent cannot accept, reject, or waive it for you. "
        "Use the Plan panel, or run mark-plan-item yourself."
    )


def work_refusal(plan: dict, kind: str) -> str:
    action = "Heavy modeling" if kind == "model" else "Simulation"
    done = "modeled" if kind == "model" else "simulated"
    revision = _current(plan)
    open_questions = sum(1 for item in revision["questions"] if item["status"] == "open")
    if open_questions == 1:
        waiting = " 1 question is still open."
    elif open_questions:
        waiting = f" {open_questions} questions are still open."
    else:
        waiting = ""
    return (
        f"{action} stays off. Plan {plan['title']!r} ({plan['id']}) is {status_phrase(plan['status'])}, "
        f"revision v{plan['current_revision']}.{waiting} "
        "It has to be approved first. The agent cannot approve it. "
        "Review the sheets, the assumptions, and the open questions in the Plan panel, then approve. "
        f"Nothing was {done}."
    )


def untrusted_refusal(plan: dict, kind: str) -> str:
    action = "Heavy modeling" if kind == "model" else "Simulation"
    done = "modeled" if kind == "model" else "simulated"
    return (
        f"{action} stays off. The plan file for {plan['title']!r} ({plan['id']}) says "
        f"{status_phrase(plan['status'])} at revision v{plan['current_revision']}, "
        "but that does not match an approval recorded on this computer. "
        "Editing the plan file does not approve it, and the agent cannot approve it. "
        "Approve it yourself in the Plan panel, or run approve-plan. "
        f"Nothing was {done}."
    )


def approvals_path() -> Path:
    """Where this computer remembers approvals. Never inside the project."""
    override = os.environ.get("AICAD_APPROVALS", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".config" / "aicad" / "plan-approvals.json").resolve()


class PlanStore:
    """Plans for one project folder. ``actor`` is ``user`` or ``agent``."""

    def __init__(self, root: str | Path, actor: str = "user"):
        self.root = Path(root).resolve()
        self.actor = actor if actor in {"user", "agent"} else "agent"
        self.directory = self.root / "plans"

    def start(
        self,
        name: str,
        title: str | None = None,
        summary: str = "",
        bundle: dict | None = None,
    ) -> dict:
        self._check_id(name)
        if bundle is not None and not isinstance(bundle, dict):
            raise PlanError("A plan bundle has to be a JSON object of research, sheets, assumptions, and questions.")
        title = _text(title or (bundle or {}).get("title") or "", "title", required=True, limit=120)
        summary = _text(summary or (bundle or {}).get("summary") or "", "summary", limit=2000)
        if self._path(name).is_file():
            raise PlanError(
                f"A plan named {name!r} is already in this project. "
                "Revise that plan, or start a new one under another name."
            )
        now = _now()
        plan = {
            "format": _FORMAT,
            "units": "mm",
            "id": name,
            "title": title,
            "summary": summary,
            "status": "draft",
            "current_revision": 1,
            "approved_revision": None,
            "approved_at": None,
            "created_at": now,
            "updated_at": now,
            "revisions": [
                _empty_revision(1, now, "Started the plan."),
            ],
        }
        plan["revisions"][0]["activity"][0]["actor"] = self.actor
        if bundle:
            _apply_bundle(plan["revisions"][0], bundle)
        self._write(plan)
        return self._public(plan)

    def summaries(self) -> list[dict]:
        directory = _plan_directory(self.root)
        if directory is None:
            return []
        found = []
        for path in sorted(directory.glob("*.json")):
            try:
                plan = self._read(path)
                revision = _current(plan)
            except (PlanError, OSError) as exc:
                found.append(_unreadable_summary(path, exc))
                continue
            view = self._approval_view(plan)
            found.append(
                {
                    "id": plan["id"],
                    "title": plan["title"],
                    "status": plan["status"],
                    "status_text": view["status_text"],
                    "pill": view["pill"],
                    "approval": view["approval"],
                    "modeling": view["modeling"],
                    "current_revision": plan["current_revision"],
                    "approved_revision": plan["approved_revision"],
                    "open_questions": _open_question_count(self.root, plan, revision),
                    "open_assumptions": _open_assumption_count(self.root, plan, revision),
                    "sheet_count": len(revision["sheets"]),
                    "updated_at": plan["updated_at"],
                }
            )
        return found

    def get(self, name: str) -> dict:
        return self._public(self._load(name))

    def add_research(self, name: str, note: str, source: str, title: str = "") -> dict:
        plan = self._load(name)
        self._require_draft(plan)
        revision = _current(plan)
        item = {
            "id": _next_id(revision, "r"),
            "title": _text(title, "title", limit=160),
            "note": _text(note, "note", required=True),
            "source": _text(source, "source", required=True, limit=500),
        }
        revision["research"].append(item)
        _activity(revision, self.actor, "research", item["id"], item["note"])
        self._touch(plan)
        return self._public(plan)

    def add_assumption(self, name: str, text: str) -> dict:
        plan = self._load(name)
        self._require_draft(plan)
        revision = _current(plan)
        item = {
            "id": _next_id(revision, "a"),
            "text": _text(text, "assumption", required=True),
            "status": "open",
            "comment": "",
        }
        revision["assumptions"].append(item)
        _activity(revision, self.actor, "assumption", item["id"], item["text"])
        self._touch(plan)
        return self._public(plan)

    def add_question(self, name: str, text: str) -> dict:
        plan = self._load(name)
        self._require_draft(plan)
        revision = _current(plan)
        item = {
            "id": _next_id(revision, "q"),
            "text": _text(text, "question", required=True),
            "status": "open",
            "answer": "",
            "comment": "",
        }
        revision["questions"].append(item)
        _activity(revision, self.actor, "question", item["id"], item["text"])
        self._touch(plan)
        return self._public(plan)

    def put_sheet(self, name: str, spec: dict) -> dict:
        plan = self._load(name)
        self._require_draft(plan)
        if not isinstance(spec, dict):
            raise PlanError("A sheet is a JSON object with blocks, dimensions, and labels.")
        revision = _current(plan)
        stored = _normalize_sheet(spec, revision)
        replaced = False
        for index, existing in enumerate(revision["sheets"]):
            if existing["id"] == stored["id"]:
                revision["sheets"][index] = stored
                replaced = True
                break
        if not replaced:
            if len(revision["sheets"]) >= _MAX_SHEETS:
                raise PlanError(f"A revision holds up to {_MAX_SHEETS} sheets.")
            revision["sheets"].append(stored)
        _activity(revision, self.actor, "sheet", stored["id"], stored["title"])
        self._touch(plan)
        return self._public(plan)

    def set_dimensions(self, name: str, dimensions: list[dict]) -> dict:
        plan = self._load(name)
        self._require_draft(plan)
        revision = _current(plan)
        revision["dimensions"] = _normalize_dimensions(dimensions)
        _activity(revision, self.actor, "dimensions", "plan", _dimension_sentence(revision["dimensions"]))
        self._touch(plan)
        return self._public(plan)

    def comment(self, name: str, item: str, text: str) -> dict:
        plan = self._load(name)
        if plan["status"] == "approved":
            raise PlanError(_frozen(plan))
        revision = _current(plan)
        target = _find_item(revision, item) if item != "plan" else None
        if item != "plan" and target is None:
            raise PlanError(f"No assumption, question, or sheet named {item!r} on revision v{revision['version']}.")
        note = _text(text, "comment", required=True)
        if target is not None and "comment" in target:
            target["comment"] = note
        _activity(revision, self.actor, "comment", item, note)
        self._touch(plan)
        return self._public(plan)

    def answer(self, name: str, item: str, text: str) -> dict:
        self._require_user(answer_refusal())
        plan = self._load(name)
        if plan["status"] == "approved":
            raise PlanError(_frozen(plan))
        revision = _current(plan)
        question = _find_kind(revision, "questions", item)
        if question is None:
            raise PlanError(f"No question named {item!r} on revision v{revision['version']}.")
        question["answer"] = _text(text, "answer", required=True)
        question["status"] = "answered"
        _activity(revision, "user", "answer", item, question["answer"])
        self._touch(plan)
        _record_entry(self.root, plan["id"], revision["version"], "answer", item, question["answer"])
        return self._public(plan)

    def mark(self, name: str, item: str, mark: str) -> dict:
        self._require_user(mark_refusal())
        plan = self._load(name)
        if plan["status"] == "approved":
            raise PlanError(_frozen(plan))
        revision = _current(plan)
        assumption = _find_kind(revision, "assumptions", item)
        question = _find_kind(revision, "questions", item)
        if assumption is None and question is None:
            raise PlanError(f"No assumption or question named {item!r} on revision v{revision['version']}.")
        choice = str(mark or "").strip().lower()
        if assumption is not None:
            if choice not in _ASSUMPTION_MARKS:
                raise PlanError("Mark an assumption open, accepted, or rejected.")
            assumption["status"] = choice
        else:
            if choice not in _QUESTION_MARKS:
                raise PlanError("Mark a question open or waived. An answer uses answer-plan-question.")
            question["status"] = choice
            if choice == "open":
                question["answer"] = ""
        _activity(revision, "user", "mark", item, choice)
        self._touch(plan)
        _record_entry(self.root, plan["id"], revision["version"], "mark", item, choice)
        return self._public(plan)

    def submit(self, name: str) -> dict:
        plan = self._load(name)
        if plan["status"] == "approved":
            raise PlanError(
                f"{plan['title']!r} is already approved at revision v{plan['approved_revision']}. "
                "Revise it if the design changed. That clears the approval."
            )
        if plan["status"] == "in_review":
            raise PlanError(f"{plan['title']!r} is already in review at revision v{plan['current_revision']}.")
        revision = _current(plan)
        missing = []
        if not revision["research"]:
            missing.append("one research note with a source")
        if not revision["sheets"]:
            missing.append("one blueprint sheet")
        if not revision["assumptions"] and not revision["questions"]:
            missing.append("one assumption or one open question")
        if missing:
            raise PlanError(
                "This draft is not ready for review. It still needs " + ", ".join(missing) + "."
            )
        plan["status"] = "in_review"
        _activity(revision, self.actor, "submit", "plan", "Submitted for review.")
        self._touch(plan)
        return self._public(plan)

    def revise(self, name: str, changed: str, bundle: dict | None = None) -> dict:
        plan = self._load(name)
        note = _text(changed, "what changed", required=True, limit=2000)
        if bundle is not None and not isinstance(bundle, dict):
            raise PlanError("A revision bundle has to be a JSON object.")
        previous = plan["status"]
        source = _current(plan)
        revision = copy.deepcopy(source)
        now = _now()
        revision["version"] = source["version"] + 1
        revision["created_at"] = now
        revision["changed"] = note
        revision["activity"] = []
        if bundle:
            _apply_bundle(revision, bundle)
        _activity(revision, self.actor, "revise", "plan", note)
        plan["revisions"].append(revision)
        plan["current_revision"] = revision["version"]
        plan["status"] = "draft"
        self._touch(plan)
        public = self._public(plan)
        if previous == "approved":
            public["note"] = (
                f"Revision v{revision['version']} is a draft. "
                "Approval of the previous revision no longer covers it. "
                "Modeling and simulation from this plan stay off until you approve v"
                f"{revision['version']}."
            )
        return public

    def approve(self, name: str) -> dict:
        plan = self._load(name) if self._path(name).is_file() else {"title": name, "current_revision": 1, "id": name}
        if self.actor != "user":
            raise PlanError(approval_refusal(plan if "status" in plan else {**plan, "title": name, "current_revision": 1}))
        plan = self._load(name)
        confirming = plan["status"] == "approved" and not _receipt_matches(self.root, plan)
        if plan["status"] == "approved" and not confirming:
            raise PlanError(
                f"{plan['title']!r} is already approved at revision v{plan['approved_revision']}."
            )
        if not confirming and plan["status"] != "in_review":
            raise PlanError(
                f"{plan['title']!r} is still a draft at revision v{plan['current_revision']}. "
                "Submit it for review before you approve it. The agent cannot approve it either."
            )
        now = _now()
        if not confirming or plan.get("approved_revision") != plan.get("current_revision"):
            plan["status"] = "approved"
            plan["approved_revision"] = plan["current_revision"]
            plan["approved_at"] = now
        revision = _current(plan)
        note = (
            f"Recorded the approval of revision v{revision['version']} on this computer."
            if confirming
            else f"Approved revision v{revision['version']}."
        )
        _activity(revision, "user", "approve", "plan", note)
        self._touch(plan)
        stored = self._load(name)
        _record_approval(self.root, stored)
        public = self._public(stored)
        open_questions = _open_question_count(self.root, stored, _current(stored))
        if open_questions == 1:
            leftover = " 1 question was still open."
        elif open_questions:
            leftover = f" {open_questions} questions were still open."
        else:
            leftover = ""
        public["note"] = (
            f"Approved revision v{revision['version']}. "
            "The dimensions for the model are listed on the right. "
            f"They are not a solid yet.{leftover}"
        )
        return public

    def start_work(self, name: str, kind: str) -> dict:
        if kind not in {"model", "simulation"}:
            raise PlanError("Work from a plan is either model or simulation.")
        plan = self._load(name)
        if plan["status"] != "approved" or plan["approved_revision"] != plan["current_revision"]:
            raise PlanError(work_refusal(plan, kind))
        if not _receipt_matches(self.root, plan):
            raise PlanError(untrusted_refusal(plan, kind))
        revision = _current(plan)
        word = "Modeling" if kind == "model" else "Simulation"
        return {
            "plan": plan["id"],
            "title": plan["title"],
            "revision": revision["version"],
            "status": "approved",
            "released": kind,
            "units": "mm",
            "dimensions": copy.deepcopy(revision["dimensions"]),
            "assumptions": [
                _present_item(self.root, plan, revision, "assumption", item) for item in revision["assumptions"]
            ],
            "research": copy.deepcopy(revision["research"]),
            "questions": [
                _present_item(self.root, plan, revision, "question", item) for item in revision["questions"]
            ],
            "views": _views(self.root, revision),
            "ready": True,
            "note": (
                f"{word} may start from {plan['title']!r} revision v{revision['version']}. "
                "Use these dimensions and assumptions. They are the approved plan, not a solid. "
                "A later revise clears this approval."
            ),
        }

    def _require_user(self, message: str) -> None:
        if self.actor != "user":
            raise PlanError(message)

    def _require_draft(self, plan: dict) -> None:
        if plan["status"] == "draft":
            return
        revision = plan["current_revision"]
        nxt = revision + 1
        raise PlanError(
            f"Plan {plan['title']!r} is {status_phrase(plan['status'])}, revision v{revision}. "
            f"The sheets and the lists stay as they are until you revise. "
            f"Call revise_plan and say what changed. That opens v{nxt} as a draft. "
            "Approval, if there was one, no longer covers the new revision."
        )

    def _check_id(self, name: str) -> None:
        if not isinstance(name, str) or not _PLAN_ID.match(name):
            raise PlanError(
                "A plan name starts with a letter and uses letters, numbers, underscore, or hyphen. "
                f"{name!r} does not."
            )

    def _path(self, name: str) -> Path:
        self._check_id(name)
        return self.directory / f"{name}.json"

    def _load(self, name: str) -> dict:
        path = self._path(name)
        if not path.is_file():
            known = ", ".join(item["id"] for item in self.summaries()) or "none yet"
            raise PlanError(f"No plan named {name!r}. Plans in this project: {known}.")
        return self._read(path)

    def _read(self, path: Path) -> dict:
        raw = _plan_text(self.root, path)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanError(f"Could not read {path.name}: {exc.msg} at line {exc.lineno}.") from exc
        if not isinstance(payload, dict) or payload.get("format") != _FORMAT:
            raise PlanError(f"{path.name} is not a plan file this version can read.")
        if payload.get("id") != path.stem:
            raise PlanError(f"{path.name} does not match the plan id inside it.")
        if payload.get("status") not in _STATUSES:
            raise PlanError(f"{path.name} has a status this version does not use.")
        if not isinstance(payload.get("title"), str) or not payload["title"].strip():
            raise PlanError(f"{path.name} has no title.")
        from aicad.labels import plain_title

        payload["title"] = plain_title(payload["title"], "title", PlanError)
        revisions = payload.get("revisions")
        if not isinstance(revisions, list) or not revisions:
            raise PlanError(f"{path.name} has no revisions to read.")
        for revision in revisions:
            _coerce_revision(revision, path.name)
            for sheet in revision.get("sheets") or []:
                _require_loaded_sheet(sheet, path.name)
        if payload.get("current_revision") != revisions[-1].get("version"):
            raise PlanError(f"{path.name} current revision does not match the last revision in the file.")
        _check_strings(payload, path.name)
        _plain_loaded(payload, path.name)
        return payload

    def _write(self, plan: dict) -> None:
        from aicad.paths import output_dir, publish_text

        self.directory = output_dir(self.root, self.directory, "Plan", PlanError)
        path = self._path(plan["id"])
        _render_revision(self.root, plan, _current(plan))
        publish_text(path, json.dumps(plan, indent=2) + "\n")
        _drop_orphan_sheets(self.root, plan)

    def _touch(self, plan: dict) -> None:
        plan["updated_at"] = _now()
        self._write(plan)

    def _public(self, plan: dict) -> dict:
        current = _current(plan)
        revisions = []
        for revision in plan["revisions"]:
            revisions.append(
                {
                    "version": revision["version"],
                    "created_at": revision["created_at"],
                    "changed": revision["changed"],
                    "research": copy.deepcopy(revision["research"]),
                    "assumptions": [
                        _present_item(self.root, plan, revision, "assumption", item)
                        for item in revision["assumptions"]
                    ],
                    "questions": [
                        _present_item(self.root, plan, revision, "question", item)
                        for item in revision["questions"]
                    ],
                    "dimensions": copy.deepcopy(revision["dimensions"]),
                    "sheets": [_sheet_public(self.root, sheet) for sheet in revision["sheets"]],
                    "activity": copy.deepcopy(revision["activity"]),
                }
            )
        view = self._approval_view(plan)
        open_questions = [
            item["id"]
            for item in current["questions"]
            if _question_still_open(self.root, plan, current, item)
        ]
        return {
            "id": plan["id"],
            "title": plan["title"],
            "summary": plan["summary"],
            "units": "mm",
            "status": plan["status"],
            "status_text": view["status_text"],
            "pill": view["pill"],
            "approval": view["approval"],
            "current_revision": plan["current_revision"],
            "approved_revision": plan["approved_revision"],
            "approved_at": plan["approved_at"],
            "updated_at": plan["updated_at"],
            "modeling": view["modeling"],
            "modeling_note": view["modeling_note"],
            "open_questions": open_questions,
            "revision": revisions[-1],
            "revisions": revisions,
            "views": _views(self.root, current),
        }

    def _approval_view(self, plan: dict) -> dict:
        recorded = _receipt_matches(self.root, plan)
        if plan["status"] == "approved" and plan.get("approved_revision") == plan.get("current_revision") and recorded:
            return {
                "approval": "recorded",
                "pill": "approved",
                "status_text": "approved",
                "modeling": "open",
                "modeling_note": (
                    "This revision is approved. start_model_from_plan and start_simulation_from_plan can use it."
                ),
            }
        if plan["status"] == "approved":
            return {
                "approval": "missing",
                "pill": "untrusted",
                "status_text": "not approved by you",
                "modeling": "closed",
                "modeling_note": (
                    "This file says approved, but this computer has no record of you approving this revision. "
                    "Editing the plan file does not approve it. Modeling and simulation stay off until you approve it here."
                ),
            }
        return {
            "approval": "none",
            "pill": plan["status"],
            "status_text": status_phrase(plan["status"]),
            "modeling": "closed",
            "modeling_note": (
                "Modeling and simulation from this plan stay off until you approve the current revision."
            ),
        }


def plan_files(root: str | Path) -> dict[str, str]:
    """JSON text of every plan, for undo. Sheet PNGs are redrawn on restore.

    A plan folder that is a link is skipped. A plan file that is a link, or
    that has another name, is not copied. A plan past the size cap is not
    copied, and restoring an older copy does not delete that file.
    """
    root_path = Path(root)
    directory = _plan_directory(root_path)
    if directory is None:
        return {}
    files = {}
    for path in sorted(directory.glob("*.json")):
        try:
            files[path.name] = _plan_text(root_path, path)
        except PlanError:
            continue
    return files


def _plan_directory(root: Path) -> Path | None:
    """The plans folder when it is a real directory inside the project.

    A symlink or junction is not listed. A missing folder is not an error.
    """
    from aicad.paths import is_reparse

    root_resolved = Path(root).resolve()
    directory = root_resolved / "plans"
    if is_reparse(directory):
        return None
    try:
        if not directory.is_dir():
            return None
        resolved = directory.resolve()
    except OSError:
        return None
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return None
    return directory


def _plan_text(root: Path, path: Path) -> str:
    """UTF-8 text of one plan file, read from the project directory.

    A link is not followed. A file with another name is not opened. More than
    the plan cap is refused before the bytes are kept.
    """
    from aicad.paths import read_checked_text

    _refuse_plan_link(root, path)
    try:
        return read_checked_text(root, path, maximum=_MAX_BYTES, label=path.name, error=PlanError)
    except PlanError as exc:
        if "too large" in str(exc):
            raise PlanError(f"{path.name} is larger than a plan file should be. It was not opened.") from exc
        if str(exc) == f"Could not read {path.name}." and isinstance(exc.__cause__, UnicodeError):
            raise PlanError(f"Could not read {path.name}: the file is not UTF-8.") from exc
        raise


def _plan_over_cap(root: Path, path: Path) -> bool:
    """True when ``path`` is a plan file the undo copy left out for size."""
    try:
        _plan_text(root, path)
    except PlanError as exc:
        return "larger than a plan file" in str(exc)
    return False


def _refuse_plan_link(root: Path, path: Path) -> None:
    """Refuse a plan file that is a link, leaves the project, or has another name."""
    from aicad.paths import is_reparse, shared_file

    name = path.name
    if is_reparse(path):
        raise PlanError(f"{name} is a link and was not opened.")
    root_resolved = Path(root).resolve()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise PlanError(f"{name} is a link and was not opened.") from exc
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PlanError(f"{name} is outside the project and was not opened.")
    if shared_file(path):
        raise PlanError(f"{name} has another name and was not opened.")


def restore_plan_files(root: str | Path, files: dict[str, str]) -> None:
    """Put plan JSON back and redraw the sheets those revisions name."""
    from aicad.paths import is_reparse, output_dir, publish_text, remove_tree

    root_path = Path(root)
    directory = root_path / "plans"
    if not files and not directory.is_dir() and not directory.is_symlink():
        return
    directory = output_dir(root_path, directory, "Plans", PlanError)
    keep = set(files)
    for path in directory.glob("*.json"):
        if path.name not in keep:
            if _plan_over_cap(root_path, path):
                continue
            folder = directory / path.stem
            path.unlink()
            if folder.is_dir() or is_reparse(folder):
                remove_tree(folder)
    for name, text in files.items():
        if Path(name).name != name or not name.endswith(".json"):
            continue
        target = directory / name
        publish_text(target, text)
        try:
            plan = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(plan, dict) and plan.get("format") == _FORMAT:
            for revision in plan.get("revisions") or []:
                _render_revision(root_path, plan, revision)
            _drop_orphan_sheets(root_path, plan)


def ledger_snapshot(root: str | Path) -> dict:
    """Approvals and answers recorded for this project, for undo.

    The ledger lives outside the project. Only records whose workspace is
    this folder are copied. Other projects in the same ledger stay out.
    """
    root_key = str(Path(root).resolve())
    ledger = _load_ledger()
    return {
        "approvals": {
            key: copy.deepcopy(record)
            for key, record in ledger["approvals"].items()
            if isinstance(record, dict) and record.get("workspace") == root_key
        },
        "entries": {
            key: copy.deepcopy(record)
            for key, record in ledger["entries"].items()
            if isinstance(record, dict) and record.get("workspace") == root_key
        },
    }


def restore_ledger_snapshot(root: str | Path, saved: dict) -> None:
    """Put this project's ledger records back. Other projects are left as they are.

    An empty change does not create the ledger file.
    """
    if not isinstance(saved, dict):
        return
    root_key = str(Path(root).resolve())
    ledger = _load_ledger()
    approvals = {
        key: record
        for key, record in ledger["approvals"].items()
        if not (isinstance(record, dict) and record.get("workspace") == root_key)
    }
    entries = {
        key: record
        for key, record in ledger["entries"].items()
        if not (isinstance(record, dict) and record.get("workspace") == root_key)
    }
    for key, record in (saved.get("approvals") or {}).items():
        if isinstance(key, str) and isinstance(record, dict) and record.get("workspace") == root_key:
            approvals[key] = copy.deepcopy(record)
    for key, record in (saved.get("entries") or {}).items():
        if isinstance(key, str) and isinstance(record, dict) and record.get("workspace") == root_key:
            entries[key] = copy.deepcopy(record)
    restored = {"format": 1, "approvals": approvals, "entries": entries}
    if restored == ledger:
        return
    _save_ledger(restored)


def _load_ledger() -> dict:
    path = approvals_path()
    if not path.is_file():
        return {"format": 1, "approvals": {}, "entries": {}}
    try:
        from aicad.file_access import read_text

        payload = json.loads(read_text(path, maximum=8 * 1024 * 1024))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"format": 1, "approvals": {}, "entries": {}}
    if not isinstance(payload, dict):
        return {"format": 1, "approvals": {}, "entries": {}}
    approvals = payload.get("approvals")
    entries = payload.get("entries")
    return {
        "format": 1,
        "approvals": approvals if isinstance(approvals, dict) else {},
        "entries": entries if isinstance(entries, dict) else {},
    }


def _save_ledger(ledger: dict) -> None:
    path = approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from aicad.paths import publish_text

    publish_text(path, json.dumps(ledger, indent=2) + "\n", root=path.parent)


def _revision_fingerprint(revision: dict) -> dict:
    sheets = []
    for sheet in revision.get("sheets") or []:
        if not isinstance(sheet, dict):
            continue
        sheets.append(
            {
                "id": sheet.get("id"),
                "title": sheet.get("title"),
                "subtitle": sheet.get("subtitle") or "",
                "panels": sheet.get("panels") or [],
                "footer": sheet.get("footer") or "",
            }
        )
    return {
        "version": revision.get("version"),
        "changed": revision.get("changed") or "",
        "research": revision.get("research") or [],
        "assumptions": revision.get("assumptions") or [],
        "questions": revision.get("questions") or [],
        "dimensions": revision.get("dimensions") or [],
        "sheets": sheets,
    }


def _content_hash(revision: dict) -> str:
    payload = json.loads(json.dumps(_revision_fingerprint(revision)))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _approval_key(root: Path, plan_id: str, content: str) -> str:
    raw = json.dumps(
        {"workspace": str(root), "plan": plan_id, "content": content},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _receipt_matches(root: Path, plan: dict) -> bool:
    if plan.get("status") != "approved" or plan.get("approved_revision") != plan.get("current_revision"):
        return False
    try:
        revision = _current(plan)
    except PlanError:
        return False
    content = _content_hash(revision)
    record = _load_ledger()["approvals"].get(_approval_key(root, plan["id"], content))
    return (
        isinstance(record, dict)
        and record.get("content") == content
        and record.get("plan") == plan["id"]
        and record.get("workspace") == str(root)
    )


def _record_approval(root: Path, plan: dict) -> None:
    revision = _current(plan)
    content = _content_hash(revision)
    ledger = _load_ledger()
    ledger["approvals"][_approval_key(root, plan["id"], content)] = {
        "workspace": str(root),
        "plan": plan["id"],
        "revision": revision["version"],
        "content": content,
        "at": _now(),
    }
    _save_ledger(ledger)


def _entry_key(root: Path, plan_id: str, version: int, kind: str, item: str, text: str) -> str:
    raw = json.dumps(
        {
            "workspace": str(root),
            "plan": plan_id,
            "revision": version,
            "kind": kind,
            "item": item,
            "text": text,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _has_entry(root: Path, plan_id: str, version: int, kind: str, item: str, text: str) -> bool:
    record = _load_ledger()["entries"].get(_entry_key(root, plan_id, version, kind, item, text))
    return isinstance(record, dict) and record.get("plan") == plan_id and record.get("text") == text


def _record_entry(root: Path, plan_id: str, version: int, kind: str, item: str, text: str) -> None:
    ledger = _load_ledger()
    ledger["entries"][_entry_key(root, plan_id, version, kind, item, text)] = {
        "workspace": str(root),
        "plan": plan_id,
        "revision": version,
        "kind": kind,
        "item": item,
        "text": text,
        "at": _now(),
    }
    _save_ledger(ledger)


def _item_trusted(root: Path, plan: dict, revision: dict, kind: str, item: dict) -> bool:
    status = item.get("status")
    if status in (None, "", "open"):
        return True
    version = revision.get("version")
    item_id = str(item.get("id") or "")
    if not isinstance(version, int):
        return False
    if kind == "question" and status == "answered":
        return _has_entry(root, plan["id"], version, "answer", item_id, str(item.get("answer") or ""))
    if status in {"accepted", "rejected", "waived"}:
        return _has_entry(root, plan["id"], version, "mark", item_id, str(status))
    return False


def _present_item(root: Path, plan: dict, revision: dict, kind: str, item: dict) -> dict:
    if not isinstance(item, dict):
        return {
            "id": "",
            "text": "",
            "status": "open",
            "trusted": False,
            "trust_note": "This item could not be read.",
        }
    shown = copy.deepcopy(item)
    trusted = _item_trusted(root, plan, revision, kind, item)
    shown["trusted"] = trusted
    if trusted:
        return shown
    shown["file_status"] = item.get("status")
    shown["status"] = "open"
    if kind == "question" and item.get("answer"):
        shown["trust_note"] = "This answer is in the file, but it was not entered on this computer."
    else:
        shown["trust_note"] = "This mark is in the file, but it was not entered on this computer."
    return shown


def _question_still_open(root: Path, plan: dict, revision: dict, item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("status") in (None, "", "open"):
        return True
    return not _item_trusted(root, plan, revision, "question", item)


def _assumption_still_open(root: Path, plan: dict, revision: dict, item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("status") in (None, "", "open"):
        return True
    return not _item_trusted(root, plan, revision, "assumption", item)


def _open_question_count(root: Path, plan: dict, revision: dict) -> int:
    return sum(1 for item in revision.get("questions") or [] if _question_still_open(root, plan, revision, item))


def _open_assumption_count(root: Path, plan: dict, revision: dict) -> int:
    return sum(1 for item in revision.get("assumptions") or [] if _assumption_still_open(root, plan, revision, item))


def _sheet_public(root: Path, sheet: dict) -> dict:
    relative = sheet.get("path") or ""
    absolute = ""
    error = ""
    if relative:
        try:
            absolute = str(_abs(root, relative))
        except PlanError:
            relative = ""
            error = "This sheet path points outside the project and was not opened."
    shown = {
        "id": sheet.get("id"),
        "title": sheet.get("title") or "",
        "subtitle": sheet.get("subtitle") or "",
        "file": relative,
        "path": absolute,
        "panels": sheet.get("panels") or [],
    }
    if error:
        shown["error"] = error
    return shown


def _unreadable_summary(path: Path, exc: Exception) -> dict:
    stem = path.stem
    return {
        "id": stem if _PLAN_ID.match(stem) else path.name,
        "title": path.name,
        "status": "unreadable",
        "status_text": "could not be read",
        "pill": "untrusted",
        "approval": "none",
        "modeling": "closed",
        "error": str(exc),
        "current_revision": None,
        "approved_revision": None,
        "open_questions": None,
        "open_assumptions": None,
        "sheet_count": 0,
        "updated_at": "",
    }


def _coerce_revision(revision: dict, filename: str) -> None:
    version = revision.get("version") if isinstance(revision, dict) else None
    if not isinstance(revision, dict) or isinstance(version, bool) or not isinstance(version, int):
        raise PlanError(f"{filename} has a revision this version cannot read.")
    for key in ("research", "assumptions", "questions", "dimensions", "sheets", "activity"):
        if key not in revision:
            revision[key] = []
        elif not isinstance(revision[key], list):
            raise PlanError(f"{filename} revision v{version} has a {key} value that is not a list.")
    if not isinstance(revision.get("changed"), str):
        revision["changed"] = "" if revision.get("changed") is None else str(revision.get("changed"))


def _check_strings(value: Any, filename: str) -> None:
    if isinstance(value, str):
        if len(value) > _MAX_TEXT:
            raise PlanError(
                f"{filename} has text longer than {_MAX_TEXT} characters. "
                "A plan file is not a place for a long document. It was not opened."
            )
        return
    if isinstance(value, dict):
        for item in value.values():
            _check_strings(item, filename)
    elif isinstance(value, list):
        for item in value:
            _check_strings(item, filename)


def _empty_revision(version: int, now: str, changed: str) -> dict:
    return {
        "version": version,
        "created_at": now,
        "changed": changed,
        "research": [],
        "assumptions": [],
        "questions": [],
        "dimensions": [],
        "sheets": [],
        "activity": [
            {"at": now, "actor": "user", "action": "start", "item": "plan", "text": changed},
        ],
    }


def _apply_bundle(revision: dict, bundle: dict) -> None:
    for item in bundle.get("research") or []:
        if not isinstance(item, dict):
            raise PlanError("Each research note is an object with a note and a source.")
        revision["research"].append(
            {
                "id": _take_id(revision, item.get("id"), "r"),
                "title": _text(item.get("title") or "", "title", limit=160),
                "note": _text(item.get("note") or "", "note", required=True),
                "source": _text(item.get("source") or "", "source", required=True, limit=500),
            }
        )
    for item in bundle.get("assumptions") or []:
        if not isinstance(item, dict):
            raise PlanError("Each assumption is an object with text.")
        revision["assumptions"].append(
            {
                "id": _take_id(revision, item.get("id"), "a"),
                "text": _text(item.get("text") or "", "assumption", required=True),
                "status": "open",
                "comment": "",
            }
        )
    for item in bundle.get("questions") or []:
        if not isinstance(item, dict):
            raise PlanError("Each question is an object with text.")
        revision["questions"].append(
            {
                "id": _take_id(revision, item.get("id"), "q"),
                "text": _text(item.get("text") or "", "question", required=True),
                "status": "open",
                "answer": "",
                "comment": "",
            }
        )
    if "dimensions" in bundle:
        revision["dimensions"] = _normalize_dimensions(bundle.get("dimensions") or [])
    incoming = bundle.get("sheets") or []
    if not isinstance(incoming, list):
        raise PlanError("sheets is a list of sheet objects.")
    for spec in incoming:
        stored = _normalize_sheet(spec, revision)
        for index, existing in enumerate(revision["sheets"]):
            if existing["id"] == stored["id"]:
                revision["sheets"][index] = stored
                break
        else:
            if len(revision["sheets"]) >= _MAX_SHEETS:
                raise PlanError(f"A revision holds up to {_MAX_SHEETS} sheets.")
            revision["sheets"].append(stored)


def _normalize_dimensions(dimensions: list) -> list[dict]:
    if not isinstance(dimensions, list):
        raise PlanError("Dimensions are a list of {name, value_mm, note}.")
    cleaned = []
    seen = set()
    for item in dimensions:
        if not isinstance(item, dict):
            raise PlanError("Each dimension is an object with name and value_mm.")
        key = _text(item.get("name") or "", "dimension name", required=True, limit=80)
        if key in seen:
            raise PlanError(f"Dimension {key!r} is listed twice.")
        seen.add(key)
        value = _number(item.get("value_mm"), "value_mm")
        cleaned.append(
            {
                "name": key,
                "value_mm": value,
                "note": _text(item.get("note") or "", "note", limit=500),
            }
        )
    return cleaned


def _normalize_sheet(spec: dict, revision: dict) -> dict:
    if not isinstance(spec, dict):
        raise PlanError("A sheet is a JSON object.")
    title = _text(spec.get("title") or "Sheet", "sheet title", required=True, limit=120)
    sheet_id = spec.get("id") or _slug(title) or _next_id(revision, "s")
    if not isinstance(sheet_id, str) or not _ITEM_ID.match(sheet_id):
        raise PlanError(
            "A sheet id is short lowercase letters, numbers, and underscores, such as 01_top. "
            f"{sheet_id!r} is not."
        )
    _refuse_reserved_sheet_id(sheet_id)
    owns_sheet = any(sheet["id"] == sheet_id for sheet in revision["sheets"])
    if not owns_sheet and sheet_id in _ids(revision):
        raise PlanError(f"Id {sheet_id!r} is already used on this revision.")
    panels = spec.get("panels")
    if panels is None:
        panels = [spec]
    if not isinstance(panels, list) or not panels or len(panels) > 4:
        raise PlanError("A sheet has one to four panels.")
    cleaned_panels = [_normalize_panel(panel) for panel in panels]
    if not any(_panel_has_body(panel) for panel in cleaned_panels):
        raise PlanError("A sheet needs a block, a dimension, a label, or a note.")
    return {
        "id": sheet_id,
        "title": title,
        "subtitle": _text(spec.get("subtitle") or "", "subtitle", limit=200),
        "footer": _text(spec.get("footer") or _FOOTER, "footer", limit=500),
        "panels": cleaned_panels,
        "path": "",
    }


def _normalize_panel(panel: dict) -> dict:
    if not isinstance(panel, dict):
        raise PlanError("Each panel is an object.")
    blocks = []
    for block in panel.get("blocks") or []:
        if not isinstance(block, dict):
            raise PlanError("Each block is an object with x, y, w, h, and a label.")
        width = _number(block.get("w"), "w")
        height = _number(block.get("h"), "h")
        if width <= 0 or height <= 0:
            raise PlanError("A block width and height have to be greater than zero.")
        blocks.append(
            {
                "x": _number(block.get("x"), "x"),
                "y": _number(block.get("y"), "y"),
                "w": width,
                "h": height,
                "label": _text(block.get("label") or "", "label", limit=80),
                "fill": _fill(block.get("fill") or "blue"),
            }
        )
    if len(blocks) > _MAX_BLOCKS:
        raise PlanError(f"A panel holds up to {_MAX_BLOCKS} blocks.")
    dimensions = []
    for dim in panel.get("dimensions") or []:
        if not isinstance(dim, dict):
            raise PlanError("Each dimension callout is an object.")
        x0, y0 = _number(dim.get("x0"), "x0"), _number(dim.get("y0"), "y0")
        x1, y1 = _number(dim.get("x1"), "x1"), _number(dim.get("y1"), "y1")
        span = math.hypot(x1 - x0, y1 - y0)
        text = _text(dim.get("text") or "", "dimension text", limit=80)
        if not text:
            text = _mm(span)
        dimensions.append(
            {
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "text": text,
                "offset": _number(dim.get("offset") if dim.get("offset") is not None else 40, "offset"),
            }
        )
    if len(dimensions) > _MAX_DIMS:
        raise PlanError(f"A panel holds up to {_MAX_DIMS} dimensions.")
    labels = []
    for label in panel.get("labels") or []:
        if not isinstance(label, dict):
            raise PlanError("Each label is an object with x, y, and text.")
        labels.append(
            {
                "x": _number(label.get("x"), "x"),
                "y": _number(label.get("y"), "y"),
                "text": _text(label.get("text") or "", "label", required=True, limit=80),
            }
        )
    notes = []
    for line in panel.get("notes") or []:
        notes.append(_text(line, "note", required=True, limit=240))
    grid = panel.get("grid_mm")
    return {
        "title": _text(panel.get("title") or "View", "panel title", limit=80),
        "blocks": blocks,
        "dimensions": dimensions,
        "labels": labels,
        "notes": notes,
        "grid_mm": _auto_grid(blocks, dimensions, labels) if grid is None else _number(grid, "grid_mm"),
    }


def _panel_has_body(panel: dict) -> bool:
    return bool(panel["blocks"] or panel["dimensions"] or panel["labels"] or panel["notes"])


def _auto_grid(blocks: list[dict], dimensions: list[dict], labels: list[dict]) -> float:
    xs = [0.0]
    ys = [0.0]
    for block in blocks:
        xs.extend((block["x"], block["x"] + block["w"]))
        ys.extend((block["y"], block["y"] + block["h"]))
    for dim in dimensions:
        xs.extend((dim["x0"], dim["x1"]))
        ys.extend((dim["y0"], dim["y1"]))
    for label in labels:
        xs.append(label["x"])
        ys.append(label["y"])
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    raw = span / 8.0
    for step in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000):
        if step >= raw:
            return float(step)
    return 5000.0


def _fill(value) -> str:
    if isinstance(value, str) and value in FILLS:
        return value
    raise PlanError("A block fill is one of: " + ", ".join(FILLS) + ".")


def _render_revision(root: Path, plan: dict, revision: dict) -> None:
    from aicad.paths import output_dir

    output_dir(root, root / "plans" / plan["id"] / f"v{revision['version']}", "Plan sheet", PlanError)
    stamp = plan.get("status") or "draft"
    if revision.get("version") != plan.get("current_revision"):
        stamp = "recorded"
    for sheet in revision.get("sheets") or []:
        if not isinstance(sheet, dict):
            raise PlanError("A plan sheet has to stay inside the project.")
        destination, relative = _sheet_destination(root, plan, revision, sheet)
        sheet["path"] = relative
        panels = []
        for panel in sheet["panels"]:
            drawn = copy.deepcopy(panel)
            for block in drawn["blocks"]:
                block["_fill"] = FILLS[block["fill"]]
            panels.append(drawn)
        destination.parent.mkdir(parents=True, exist_ok=True)
        render_plan_sheet(
            destination,
            title=sheet["title"],
            subtitle=sheet.get("subtitle") or "",
            revision=int(revision["version"]),
            status=stamp,
            panels=panels,
            footer=sheet.get("footer") or _FOOTER,
        )


def _refuse_reserved_sheet_id(sheet_id: str) -> None:
    from aicad.paths import is_reserved_device_name

    if is_reserved_device_name(sheet_id):
        raise PlanError(f"{sheet_id} is reserved on Windows. Pick another sheet id.")


def _require_loaded_sheet(sheet: object, filename: str) -> None:
    """Reject a sheet id that cannot be drawn inside the project. The picture is not opened."""
    if not isinstance(sheet, dict):
        raise PlanError(f"{filename} has a sheet this version cannot read.")
    sheet_id = sheet.get("id")
    if not isinstance(sheet_id, str) or not _ITEM_ID.match(sheet_id):
        raise PlanError(f"{filename} has a sheet this version cannot read.")
    _refuse_reserved_sheet_id(sheet_id)


def _sheet_destination(root: Path, plan: dict, revision: dict, sheet: dict) -> tuple[Path, str]:
    """Picture path for one sheet. A name that leaves the project is refused before any write."""
    plan_id = plan.get("id")
    version = revision.get("version")
    sheet_id = sheet.get("id")
    if (
        not isinstance(plan_id, str)
        or not _PLAN_ID.match(plan_id)
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or not isinstance(sheet_id, str)
        or not _ITEM_ID.match(sheet_id)
    ):
        raise PlanError("A plan sheet has to stay inside the project.")
    _refuse_reserved_sheet_id(sheet_id)
    relative = f"plans/{plan_id}/v{version}/{sheet_id}.png"
    parent = _abs(root, f"plans/{plan_id}/v{version}")
    destination = parent / f"{sheet_id}.png"
    from aicad.paths import is_reparse, shared_file

    try:
        if is_reparse(destination) or shared_file(destination):
            destination.unlink()
    except OSError as exc:
        raise PlanError("A plan sheet has to stay inside the project.") from exc
    return destination, relative


def _drop_orphan_sheets(root: Path, plan: dict) -> None:
    from aicad.paths import resolve_in_project

    folder = root / "plans" / plan["id"]
    if not folder.exists() and not folder.is_symlink():
        return
    folder = resolve_in_project(root, folder, "Plan", PlanError)
    if not folder.is_dir():
        return
    from aicad.paths import stays_inside

    referenced = set()
    for revision in plan.get("revisions") or []:
        for sheet in revision.get("sheets") or []:
            if sheet.get("path"):
                candidate = root / sheet["path"]
                if stays_inside(root, candidate):
                    referenced.add(candidate.resolve())
    for png in folder.rglob("*.png"):
        if not stays_inside(root, png):
            continue
        if png.resolve() not in referenced:
            png.unlink()
    for child in sorted(folder.rglob("*"), reverse=True):
        if child.is_symlink() or not stays_inside(root, child):
            continue
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()


def _views(root: Path, revision: dict) -> list[dict]:
    views = []
    for sheet in revision.get("sheets") or []:
        if not isinstance(sheet, dict) or not sheet.get("path"):
            continue
        shown = _sheet_public(root, sheet)
        if not shown["file"]:
            continue
        views.append(
            {
                "name": shown["id"],
                "title": shown["title"],
                "file": shown["file"],
                "path": shown["path"],
            }
        )
    return views


def _abs(root: Path, relative: str) -> Path:
    from aicad.paths import resolve_in_project

    try:
        return resolve_in_project(root, Path(root) / relative, "Plan sheet", PlanError)
    except PlanError as exc:
        raise PlanError("A plan sheet has to stay inside the project.") from exc


def _current(plan: dict) -> dict:
    version = plan["current_revision"]
    for revision in plan["revisions"]:
        if revision["version"] == version:
            return revision
    raise PlanError(f"Revision v{version} is missing from the plan file.")


def _find_kind(revision: dict, kind: str, item_id: str) -> dict | None:
    for item in revision[kind]:
        if item["id"] == item_id:
            return item
    return None


def _find_item(revision: dict, item_id: str) -> dict | None:
    for kind in ("assumptions", "questions", "research", "sheets"):
        found = _find_kind(revision, kind, item_id)
        if found is not None:
            return found
    return None


def _next_id(revision: dict, prefix: str) -> str:
    existing = _ids(revision)
    number = 1
    while f"{prefix}{number}" in existing:
        number += 1
    return f"{prefix}{number}"


def _take_id(revision: dict, requested, prefix: str) -> str:
    if requested in (None, ""):
        return _next_id(revision, prefix)
    if not isinstance(requested, str) or not _ITEM_ID.match(requested):
        raise PlanError(f"Id {requested!r} should be short lowercase letters, numbers, and underscores.")
    if requested in _ids(revision):
        raise PlanError(f"Id {requested!r} is already used on this revision.")
    return requested


def _ids(revision: dict) -> set[str]:
    found = set()
    for kind in ("research", "assumptions", "questions", "sheets"):
        for item in revision[kind]:
            found.add(item["id"])
    return found


def _activity(revision: dict, actor: str, action: str, item: str, text: str) -> None:
    revision["activity"].append(
        {"at": _now(), "actor": actor, "action": action, "item": item, "text": text}
    )


def _frozen(plan: dict) -> str:
    return (
        f"{plan['title']!r} is approved at revision v{plan['approved_revision']}. "
        "Revise it to change the questions or the sheets. "
        "That opens a new draft, and modeling stays off until you approve the new revision."
    )


def _dimension_sentence(dimensions: list[dict]) -> str:
    if not dimensions:
        return "Cleared the dimensions."
    parts = [f"{item['name']} { _mm(item['value_mm']) }" for item in dimensions]
    return "Dimensions: " + ", ".join(parts)


def _slug(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return cleaned[:24]


def _plain_loaded(payload: dict, filename: str) -> None:
    """Stored plan text shows the same characters that were saved.

    A bidi control is removed. A letter that looks like a different letter is
    refused, including in a sheet, a research note, and the text a receipt shows.
    """

    from aicad.labels import spoofs_latin, strip_hidden

    def show(value: str, label: str) -> str:
        cleaned = strip_hidden(value)
        if spoofs_latin(cleaned):
            raise PlanError(f"{filename} {label} has a character that looks like a different letter.")
        return cleaned

    def assign(container: dict, key: str, label: str) -> None:
        if not isinstance(container, dict) or key not in container:
            return
        current = container[key]
        if not isinstance(current, str):
            return
        container[key] = show(current, label)

    assign(payload, "title", "title")
    assign(payload, "summary", "summary")
    for revision in payload.get("revisions") or []:
        if not isinstance(revision, dict):
            continue
        assign(revision, "changed", "changed")
        for item in revision.get("research") or []:
            if isinstance(item, dict):
                assign(item, "title", "title")
                assign(item, "note", "note")
                assign(item, "source", "source")
        for item in revision.get("assumptions") or []:
            if isinstance(item, dict):
                assign(item, "text", "assumption")
                assign(item, "comment", "comment")
        for item in revision.get("questions") or []:
            if isinstance(item, dict):
                assign(item, "text", "question")
                assign(item, "answer", "answer")
                assign(item, "comment", "comment")
        for item in revision.get("dimensions") or []:
            if isinstance(item, dict):
                assign(item, "name", "dimension name")
                assign(item, "note", "note")
        for item in revision.get("activity") or []:
            if isinstance(item, dict):
                assign(item, "text", "activity")
        for sheet in revision.get("sheets") or []:
            if not isinstance(sheet, dict):
                continue
            assign(sheet, "title", "sheet title")
            assign(sheet, "subtitle", "subtitle")
            assign(sheet, "footer", "footer")
            for panel in sheet.get("panels") or []:
                if not isinstance(panel, dict):
                    continue
                assign(panel, "title", "panel title")
                for block in panel.get("blocks") or []:
                    if isinstance(block, dict):
                        assign(block, "label", "label")
                for dim in panel.get("dimensions") or []:
                    if isinstance(dim, dict):
                        assign(dim, "text", "dimension text")
                for label in panel.get("labels") or []:
                    if isinstance(label, dict):
                        assign(label, "text", "label")
                notes = panel.get("notes")
                if isinstance(notes, list):
                    panel["notes"] = [show(line, "note") if isinstance(line, str) else line for line in notes]


def _text(value: Any, label: str, required: bool = False, limit: int = _MAX_TEXT) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise PlanError(f"{label} has to be text.")
    from aicad.labels import plain_title

    value = plain_title(value, label, PlanError)
    if required and not value:
        raise PlanError(f"{label} is required.")
    if len(value) > limit:
        raise PlanError(f"{label} is longer than {limit} characters.")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanError(f"{label} has to be a number, in millimeters.")
    number = float(value)
    if not math.isfinite(number):
        raise PlanError(f"{label} has to be a finite number.")
    return number


def _mm(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return f"{int(round(value))} mm"
    return f"{value:.1f} mm"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
