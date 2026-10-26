"""The documentation guard rejects planning notes and banned words.

The real tree is checked as it will be committed. Synthetic cases cover a
path outside the list, a banned word, an override comment, and lookalike words.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _check():
    path = ROOT / "tools" / "check_docs.py"
    spec = importlib.util.spec_from_file_location("check_docs", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repository_documents_stay_on_the_allowlist():
    errors = _check().scan_repository(ROOT)
    assert errors == []


def test_a_planning_document_outside_the_list_fails():
    errors = _check().scan_files([("docs/business/MONETIZATION.md", "notes\n")])
    assert errors == ["docs/business/MONETIZATION.md: text document is not on the software-docs allowlist"]


def test_future_software_docs_are_allowed():
    check = _check()
    errors = check.scan_files(
        [
            ("docs/guides/install.md", "Install from a virtualenv.\n"),
            ("docs/reference/cli.md", "The command list.\n"),
            ("docs/roadmap/motion.md", "Motion is queued.\n"),
            ("docs/decisions/2026-10-24-example.md", "A decision.\n"),
            ("docs/tools/inspect-part.md", "A tool.\n"),
            ("docs/security/report-privately.md", "A security guarantee.\n"),
        ]
    )
    assert errors == []


def test_banned_keywords_fail_without_an_override():
    text = "\n".join(
        [
            "A showcase draft.",
            "See the launch plan.",
            "Discuss pricing, revenue, and an investor.",
            "This is about monetization and funding.",
            "A sponsorship and a donation.",
        ]
    )
    errors = _check().scan_files([("README.md", text)])
    found = {line.split(": banned keyword ")[1].strip("'") for line in errors}
    assert found == {
        "showcase",
        "launch plan",
        "pricing",
        "revenue",
        "investor",
        "monetization",
        "funding",
        "sponsorship",
        "donation",
    }


def test_override_comment_covers_the_same_line_and_the_next():
    text = "<!-- docs-allow: showcase -->\nThe showcase word is a false positive here.\n"
    assert _check().scan_files([("README.md", text)]) == []


def test_override_does_not_cover_a_later_paragraph():
    text = "<!-- docs-allow: showcase -->\n\nThe showcase word is back.\n"
    errors = _check().scan_files([("README.md", text)])
    assert len(errors) == 1
    assert "showcase" in errors[0]


def test_price_and_a_unit_cost_are_not_pricing():
    text = "The handbook uses a price of $20/kg.\n"
    assert _check().scan_files([("docs/ROADMAP.md", text)]) == []


def test_private_and_notes_cannot_be_committed():
    errors = _check().scan_files(
        [
            ("private/plan.md", "local only\n"),
            ("notes/todo.txt", "local only\n"),
        ]
    )
    assert len(errors) == 2
    assert all("cannot be committed" in error for error in errors)
