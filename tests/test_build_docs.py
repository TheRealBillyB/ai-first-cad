"""The master documents are generated from entry files.

These tests cover sort order, a round-trip of migrated rows, entry format
validation, and Windows path separators. They do not require the committed
master to match the entries: a pull request may add an entry without editing
the master.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

BACKTICK_DECISION = "A Windows export is judged after trailing dots and spaces are removed"
BACKTICK_RATIONALE = (
    "``.. .`` is ``..`` once Win32 trims the name. That path is refused before anything is written."
)


def _build():
    path = ROOT / "tools" / "build_docs.py"
    spec = importlib.util.spec_from_file_location("build_docs", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses look up the module in sys.modules while the class is created.
    sys.modules["build_docs"] = module
    spec.loader.exec_module(module)
    return module


def _decision(day: str = "2026-10-24", slug: str = "alpha", title: str = "Alpha", rationale: str = "Because alpha.", order: int | None = None) -> str:
    lines = ["---", f"date: {day}"]
    if order is not None:
        lines.append(f"order: {order}")
    lines.extend(["---", "", f"# {title}", "", rationale, ""])
    return "\n".join(lines)


def _tool(title: str = "`inspect_part`", summary: str = "Measure it.", order: int | None = None) -> str:
    lines = []
    if order is not None:
        lines.extend(["---", f"order: {order}", "---", ""])
    lines.extend([f"# {title}", "", summary, ""])
    return "\n".join(lines)


def _write(root: Path, relative: str, text: str) -> None:
    build = _build()
    path = build.entry_directory(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "decisions").mkdir()
    (root / "docs" / "tools").mkdir()
    (root / "README.md").write_text(
        "# Guide\n\n### Tools\n\n"
        "| Tool | What it does |\n| --- | --- |\n"
        "| `inspect_part` | Measure it. |\n\n"
        "Keep this paragraph.\n",
        encoding="utf-8",
    )
    return root


def test_repository_paths_accept_windows_separators_and_reject_escapes():
    build = _build()
    assert build.normalize_repo_path("docs\\decisions\\2026-10-24-alpha.md") == (
        "docs/decisions/2026-10-24-alpha.md"
    )
    assert build.normalize_repo_path("docs/decisions/2026-10-24-alpha.md") == (
        "docs/decisions/2026-10-24-alpha.md"
    )
    with pytest.raises(ValueError):
        build.normalize_repo_path("..\\docs\\decisions\\x.md")
    with pytest.raises(ValueError):
        build.normalize_repo_path("C:\\docs\\decisions\\x.md")
    with pytest.raises(ValueError):
        build.normalize_repo_path("/tmp/docs/decisions/x.md")


def test_entry_directory_joins_windows_separators(tmp_path):
    build = _build()
    root = tmp_path / "repo"
    folder = build.entry_directory(root, "docs\\decisions")
    assert folder == root / "docs" / "decisions"
    folder.mkdir(parents=True)
    (folder / "2026-10-24-alpha.md").write_text(
        _decision(),
        encoding="utf-8",
        newline="\n",
    )
    (root / "docs" / "tools").mkdir()
    entries, errors = build.load_decisions(root)
    assert errors == []
    assert [entry.filename for entry in entries] == ["2026-10-24-alpha.md"]
    assert build.repo_relative(root, folder / "2026-10-24-alpha.md") == (
        "docs/decisions/2026-10-24-alpha.md"
    )


def test_write_text_strips_cr_and_uses_lf(tmp_path):
    build = _build()
    path = tmp_path / "docs" / "DECISIONS.md"
    build.write_text(path, "alpha\r\nbeta\rgamma\n")
    assert path.read_bytes() == b"alpha\nbeta\ngamma\n"


def test_crlf_checkout_check_matches_lf_and_rebuild_writes_lf(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    _write(root, "docs/decisions/2026-10-24-alpha.md", _decision())
    _write(root, "docs/tools/inspect-part.md", _tool(order=1))
    assert build.build(root) == []
    lf_decisions = (root / "docs" / "DECISIONS.md").read_bytes()
    lf_readme = (root / "README.md").read_bytes()
    assert b"\r" not in lf_decisions
    assert b"\r" not in lf_readme
    for path in root.rglob("*.md"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert build.check(root) == []
    assert build.main(["--check", "--root", str(root)]) == 0
    assert build.build(root) == []
    assert (root / "docs" / "DECISIONS.md").read_bytes() == lf_decisions
    assert (root / "README.md").read_bytes() == lf_readme


def test_git_attributes_check_out_generated_docs_as_lf():
    paths = ["docs/DECISIONS.md", "README.md"]
    decision = next((ROOT / "docs" / "decisions").glob("*.md"))
    tool = next((ROOT / "docs" / "tools").glob("*.md"))
    paths.append(decision.relative_to(ROOT).as_posix())
    paths.append(tool.relative_to(ROOT).as_posix())
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for path in paths:
        assert f"{path}: eol: lf" in result.stdout.splitlines()


def test_crlf_entries_match_lf_entries(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    text = _decision().replace("\n", "\r\n")
    path = root / "docs" / "decisions" / "2026-10-24-alpha.md"
    path.write_bytes(text.encode("utf-8"))
    entries, errors = build.load_decisions(root)
    assert errors == []
    assert entries[0].decision == "Alpha"
    assert entries[0].rationale == "Because alpha."


def test_stable_order_does_not_follow_directory_order(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    files = {
        "2026-10-22-b.md": _decision("2026-10-22", "b", "Second older", "Older b.", order=2),
        "2026-10-24-m.md": _decision("2026-10-24", "m", "Middle", "New m."),
        "2026-10-23-a.md": _decision("2026-10-23", "a", "Kept", "Kept row.", order=1),
        "2026-10-24-b.md": _decision("2026-10-24", "b", "Before middle", "New b."),
        "2026-10-22-a.md": _decision("2026-10-22", "a", "First older", "Older a.", order=1),
        "2026-10-23-m.md": _decision("2026-10-23", "m", "Ahead of kept", "New on the old day."),
    }
    # Write in an order that is not the rendered order.
    for name in reversed(sorted(files)):
        _write(root, f"docs/decisions/{name}", files[name])
    entries, errors = build.load_decisions(root)
    assert errors == []
    first = build.render_decisions(entries)
    second = build.render_decisions(list(reversed(entries)))
    assert first == second
    assert b"\r" not in first.encode("utf-8")
    assert build.parse_decision_tables(first) == [
        ("2026-10-24", "Before middle", "New b."),
        ("2026-10-24", "Middle", "New m."),
        ("2026-10-23", "Ahead of kept", "New on the old day."),
        ("2026-10-23", "Kept", "Kept row."),
        ("2026-10-22", "First older", "Older a."),
        ("2026-10-22", "Second older", "Older b."),
    ]


def test_shared_order_ties_break_by_filename(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    _write(root, "docs/decisions/2026-10-24-b.md", _decision("2026-10-24", "b", "Bee", "B.", order=1))
    _write(root, "docs/decisions/2026-10-24-a.md", _decision("2026-10-24", "a", "Aye", "A.", order=1))
    _write(root, "docs/tools/zeta.md", _tool("`zeta`", "Last.", order=1))
    _write(root, "docs/tools/alpha.md", _tool("`alpha`", "First.", order=1))
    _write(root, "docs/tools/middle.md", _tool("`middle`", "Appended."))
    decisions, _ = build.load_decisions(root)
    tools, errors = build.load_tools(root)
    assert errors == []
    assert [entry.decision for entry in build.sort_decisions(decisions)] == ["Aye", "Bee"]
    assert [entry.tool for entry in build.sort_tools(tools)] == ["`alpha`", "`zeta`", "`middle`"]


def test_legacy_log_round_trips_including_pipes_and_backticks():
    build = _build()
    log = "\n".join(
        [
            "# Decisions",
            "",
            "Old header that the generator replaces.",
            "",
            "## 2026-10-24",
            "",
            "| Decision | Rationale |",
            "| --- | --- |",
            f"| {BACKTICK_DECISION} | {BACKTICK_RATIONALE} |",
            r"| Keep a \| in the cell | Both sides \| stay. |",
            "",
            "## 2026-10-19",
            "",
            "| Decision | Rationale |",
            "| --- | --- |",
            "| Millimeters everywhere | One unit avoids silent conversions. |",
            "",
            "## Related",
            "",
            "- [Principles](PRINCIPLES.md)",
            "",
        ]
    )
    rows = build.parse_decision_tables(log)
    entries = build.decision_entries_from_log(log)
    rendered = build.render_decisions(entries)
    assert build.parse_decision_tables(rendered) == rows
    assert rows[0] == ( "2026-10-24", BACKTICK_DECISION, BACKTICK_RATIONALE)
    assert rows[1] == ("2026-10-24", "Keep a | in the cell", "Both sides | stay.")
    again = build.decision_entries_from_log(rendered)
    assert [(entry.date.isoformat(), entry.decision, entry.rationale) for entry in again] == rows
    parsed = build.parse_decision_file(entries[0].filename, build.format_decision_file(entries[0]))
    assert parsed.decision == BACKTICK_DECISION
    assert parsed.rationale == BACKTICK_RATIONALE


def test_slug_collision_uses_a_content_hash_not_a_sequence_number():
    build = _build()
    used: set[str] = set()
    first = build.unique_slug("Hello world", used)
    second = build.unique_slug("Hello world!", used)
    assert first == "hello-world"
    assert second != "hello-world-2"
    assert second.startswith("hello-world-")
    suffix = second.removeprefix("hello-world-")
    assert len(suffix) == 8
    assert suffix.isalnum()
    assert suffix == suffix.lower()
    assert build.unique_slug("CON", set()) == "con-entry"


def test_align_inserts_a_new_top_row_and_keeps_the_old_filename():
    build = _build()
    existing = build.decision_entries_from_log(
        "\n".join(
            [
                "## 2026-10-24",
                "",
                "| Decision | Rationale |",
                "| --- | --- |",
                "| Beta stays | Because beta. |",
                "| Gamma stays | Because gamma. |",
                "",
            ]
        )
    )
    beta_name = next(entry.filename for entry in existing if entry.decision == "Beta stays")
    rows = [
        ("2026-10-24", "Alpha arrives", "Because alpha."),
        ("2026-10-24", "Beta stays", "Because beta."),
        ("2026-10-24", "Gamma stays", "Because gamma."),
    ]
    aligned = build.align_decision_entries(existing, rows)
    rendered = build.render_decisions(aligned)
    assert build.parse_decision_tables(rendered) == rows
    kept = next(entry for entry in aligned if entry.decision == "Beta stays")
    assert kept.filename == beta_name
    added = next(entry for entry in aligned if entry.decision == "Alpha arrives")
    assert added.filename != beta_name
    assert added.order == 1


def test_tool_table_round_trip_preserves_the_surrounding_prose():
    build = _build()
    readme = "\n".join(
        [
            "# Guide",
            "",
            "Intro stays.",
            "",
            "### Tools",
            "",
            "| Tool | What it does |",
            "| --- | --- |",
            "| `inspect_part` | Measure it. |",
            "| `solve_gear_train` | Speed and torque. |",
            "",
            "Keep this paragraph.",
            "",
        ]
    )
    rows = build.parse_tool_table(readme)
    entries = build.tool_entries_from_table(readme)
    updated = build.update_readme(readme, entries)
    assert build.parse_tool_table(updated) == rows
    assert updated.startswith("# Guide\n\nIntro stays.\n\n### Tools\n\n")
    assert updated.endswith("\n\nKeep this paragraph.\n")
    assert build.TOOLS_START in updated
    again = build.update_readme(updated, entries)
    assert again == updated


def test_entry_format_errors_use_forward_slashes(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    cases = {
        "2026-10-24-Alpha.md": _decision(),
        "2026-13-01-bad.md": _decision("2026-13-01"),
        "2026-10-24-date-mismatch.md": _decision("2026-10-23"),
        "2026-10-24-two-lines.md": "---\ndate: 2026-10-24\n---\n\n# Title\n\nFirst.\nSecond.\n",
        "2026-10-24-unknown.md": "---\ndate: 2026-10-24\nauthor: someone\n---\n\n# Title\n\nBecause.\n",
        "2026-10-24-zero.md": "---\ndate: 2026-10-24\norder: 0\n---\n\n# Title\n\nBecause.\n",
        "2026-10-24-pad.md": "---\ndate: 2026-10-24\norder: 01\n---\n\n# Title\n\nBecause.\n",
        "not-a-date.md": _decision(),
    }
    expected = {
        "2026-10-24-Alpha.md": "lowercase",
        "2026-13-01-bad.md": "real day",
        "2026-10-24-date-mismatch.md": "match the file name",
        "2026-10-24-two-lines.md": "one rationale line",
        "2026-10-24-unknown.md": "unknown front matter",
        "2026-10-24-zero.md": "positive integer",
        "2026-10-24-pad.md": "leading zero",
        "not-a-date.md": "YYYY-MM-DD",
    }
    for name, text in cases.items():
        _write(root, f"docs/decisions/{name}", text)
    _entries, errors = build.load_decisions(root)
    found = {error.split(":", 1)[0]: error for error in errors}
    for name, snippet in expected.items():
        key = f"docs/decisions/{name}"
        assert key in found, errors
        assert snippet in found[key]
        assert "\\" not in found[key]


def test_reserved_windows_tool_name_is_rejected_without_creating_it():
    build = _build()
    with pytest.raises(build.FormatProblem, match="reserved device name"):
        build.parse_tool_file("nul.md", _tool())


def test_byte_order_mark_is_rejected(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    path = root / "docs" / "decisions" / "2026-10-24-alpha.md"
    path.write_bytes(b"\xef\xbb\xbf" + _decision().encode("utf-8"))
    _entries, errors = build.load_decisions(root)
    assert errors == ["docs/decisions/2026-10-24-alpha.md: byte order mark is not allowed"]


def test_check_ignores_a_stale_master_and_build_rewrites_it(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    _write(root, "docs/decisions/2026-10-24-alpha.md", _decision())
    _write(root, "docs/tools/inspect-part.md", _tool(order=1))
    readme = (root / "README.md").read_text(encoding="utf-8")
    marked = build.update_readme(readme, build.tool_entries_from_table(readme))
    (root / "README.md").write_text(marked.replace("Measure it.", "STALE TABLE"), encoding="utf-8", newline="\n")
    (root / "docs" / "DECISIONS.md").write_text("STALE MASTER\n", encoding="utf-8", newline="\n")
    assert build.check(root) == []
    assert build.main(["--check", "--root", str(root)]) == 0
    assert build.build(root) == []
    decisions = (root / "docs" / "DECISIONS.md").read_bytes()
    readme_bytes = (root / "README.md").read_bytes()
    assert b"\r" not in decisions
    assert b"\r" not in readme_bytes
    assert b"STALE" not in decisions
    assert b"STALE" not in readme_bytes
    assert b"Because alpha." in decisions
    assert build.build(root) == []
    assert (root / "docs" / "DECISIONS.md").read_bytes() == decisions
    assert (root / "README.md").read_bytes() == readme_bytes
    text = decisions.decode("utf-8")
    assert "generated by `tools/build_docs.py`" in text
    assert "docs/decisions/" in text
    assert "Do not edit this file by hand." in text


def test_missing_markers_fail_the_check(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    _write(root, "docs/decisions/2026-10-24-alpha.md", _decision())
    _write(root, "docs/tools/inspect-part.md", _tool())
    errors = build.check(root)
    assert errors == ["README.md: missing generated tools markers"]
    assert build.main(["--check", "--root", str(root)]) == 1


def test_an_extra_file_in_the_entry_folder_is_an_error(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    (root / "docs" / "decisions" / "notes.txt").write_text("nope\n", encoding="utf-8")
    _entries, errors = build.load_decisions(root)
    assert errors == ["docs/decisions/notes.txt: entry folder only holds markdown entry files"]


def test_repository_entries_round_trip_and_keep_migrated_rows():
    build = _build()
    decisions, decision_errors = build.load_decisions(ROOT)
    tools, tool_errors = build.load_tools(ROOT)
    assert decision_errors == []
    assert tool_errors == []
    assert len(decisions) >= 122
    assert len(tools) >= 43
    rendered = build.render_decisions(decisions)
    rows = [(entry.date.isoformat(), entry.decision, entry.rationale) for entry in build.sort_decisions(decisions)]
    assert build.parse_decision_tables(rendered) == rows
    tool_rows = [(entry.tool, entry.summary) for entry in build.sort_tools(tools)]
    assert build.parse_tool_table(build.render_tools_block(tools)) == tool_rows
    texts = {entry.decision: entry.rationale for entry in decisions}
    assert texts[BACKTICK_DECISION] == BACKTICK_RATIONALE
    assert "Millimeters everywhere" in texts
    assert any("static manifest" in decision for decision in texts)
    assert (
        "A gear train is shafts, meshes, and differentials on the assembly, "
        "and tooth counts come from the generated gears"
    ) in texts
    assert any(entry.tool == "`solve_gear_train`" for entry in tools)
    assert build.sort_tools(tools)[0].tool == "`create_sketch` / `get_sketch`"
    assert build.check(ROOT) == []
    assert b"\r" not in (ROOT / "docs" / "DECISIONS.md").read_bytes()
    assert "\\" not in (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8").split("docs/decisions", 1)[0]


def _ready(root: Path) -> None:
    _write(root, "docs/decisions/2026-10-24-alpha.md", _decision())
    _write(root, "docs/tools/inspect-part.md", _tool())


def _symlink(path: Path, target: Path, *, directory: bool = False) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("this machine cannot create a symlink")


def test_a_symlink_master_is_not_written(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "DECISIONS.md"
    canary.write_text("KEEP\n", encoding="utf-8")
    _symlink(root / "docs" / "DECISIONS.md", canary)
    _ready(root)
    errors = build.build(root)
    assert errors
    assert any("link is not followed" in error for error in errors)
    assert canary.read_text(encoding="utf-8") == "KEEP\n"
    readme = outside / "README.md"
    readme.write_text((root / "README.md").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "docs" / "DECISIONS.md").unlink()
    (root / "README.md").unlink()
    _symlink(root / "README.md", readme)
    before = readme.read_text(encoding="utf-8")
    errors = build.build(root)
    assert errors
    assert readme.read_text(encoding="utf-8") == before


def test_a_symlink_entry_is_not_read_into_the_master(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "2026-10-24-alpha.md"
    source.write_text(_decision(title="Outside title", rationale="Outside rationale."), encoding="utf-8", newline="\n")
    _symlink(root / "docs" / "decisions" / "2026-10-24-alpha.md", source)
    _write(root, "docs/tools/inspect-part.md", _tool())
    (root / "docs" / "DECISIONS.md").write_text("OLD\n", encoding="utf-8")
    errors = build.build(root)
    assert errors
    assert any("link is not followed" in error for error in errors)
    assert (root / "docs" / "DECISIONS.md").read_text(encoding="utf-8") == "OLD\n"
    assert "Outside title" not in (root / "docs" / "DECISIONS.md").read_text(encoding="utf-8")


def test_a_symlinked_entry_directory_is_not_used(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    outside = tmp_path / "outside"
    (outside / "decisions").mkdir(parents=True)
    (outside / "decisions" / "2026-10-24-alpha.md").write_text(
        _decision(title="Outside title", rationale="Outside rationale."),
        encoding="utf-8",
        newline="\n",
    )
    (outside / "keep.txt").write_text("KEEP\n", encoding="utf-8")
    (root / "docs" / "decisions").rmdir()
    _symlink(root / "docs" / "decisions", outside / "decisions", directory=True)
    _write(root, "docs/tools/inspect-part.md", _tool())
    errors = build.build(root)
    assert errors
    assert not (outside / "DECISIONS.md").exists()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "KEEP\n"
    master = root / "docs" / "DECISIONS.md"
    assert not master.exists() or "Outside title" not in master.read_text(encoding="utf-8")


def test_a_hard_linked_master_is_not_written(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "DECISIONS.md"
    canary.write_text("KEEP\n", encoding="utf-8")
    try:
        os.link(canary, root / "docs" / "DECISIONS.md")
    except OSError:
        pytest.skip("this machine cannot create a hard link")
    _ready(root)
    errors = build.build(root)
    assert errors
    assert any("link is not followed" in error for error in errors)
    assert canary.read_text(encoding="utf-8") == "KEEP\n"


def test_a_hard_linked_entry_is_not_read(tmp_path):
    build = _build()
    root = _tree(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text(_decision(title="Outside title", rationale="Outside rationale."), encoding="utf-8", newline="\n")
    try:
        os.link(outside, root / "docs" / "decisions" / "2026-10-24-alpha.md")
    except OSError:
        pytest.skip("this machine cannot create a hard link")
    _write(root, "docs/tools/inspect-part.md", _tool())
    errors = build.build(root)
    assert errors
    assert any("link is not followed" in error for error in errors)
    master = root / "docs" / "DECISIONS.md"
    assert not master.exists() or "Outside title" not in master.read_text(encoding="utf-8")


def test_a_symlinked_parent_of_the_repo_still_builds(tmp_path):
    build = _build()
    real = tmp_path / "real"
    real.mkdir()
    via = tmp_path / "via"
    _symlink(via, real, directory=True)
    root = _tree(via)
    _ready(root)
    assert build.build(root) == []
    assert "Alpha" in (real / "repo" / "docs" / "DECISIONS.md").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows directory link")
def test_a_junction_is_not_followed(tmp_path):
    build = _build()
    outside = tmp_path / "outside"
    (outside / "decisions").mkdir(parents=True)
    (outside / "decisions" / "2026-10-24-alpha.md").write_text(
        _decision(title="Outside title", rationale="Outside rationale."),
        encoding="utf-8",
        newline="\n",
    )
    (outside / "keep.txt").write_text("KEEP\n", encoding="utf-8")
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "tools").mkdir()
    (root / "README.md").write_text(
        "# Guide\n\n| Tool | What it does |\n| --- | --- |\n| `inspect_part` | Measure it. |\n",
        encoding="utf-8",
    )
    link = root / "docs" / "decisions"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside / "decisions")],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("this machine cannot create a junction")
    _write(root, "docs/tools/inspect-part.md", _tool())
    errors = build.build(root)
    assert errors
    assert any("link is not followed" in error for error in errors)
    assert not (outside / "DECISIONS.md").exists()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "KEEP\n"
    master = root / "docs" / "DECISIONS.md"
    assert not master.exists() or "Outside title" not in master.read_text(encoding="utf-8")
