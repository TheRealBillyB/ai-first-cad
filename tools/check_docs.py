#!/usr/bin/env python3
"""Fail when a text document is outside the software-docs allowlist.

Also fail when an allowed document contains a business or launch word.
A same-line or previous-line marker skips one false positive:

    <!-- docs-allow: showcase -->

The marker names the keyword, or `*` for every keyword on that line.
Tracked files under private/ or notes/ fail even when they are not documents.
Gitignored files in those folders are not scanned.

    python3 tools/check_docs.py
    python3 tools/check_docs.py --staged
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Exact paths that may be committed. Future software docs use the prefixes.
EXACT = frozenset(
    {
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "START_HERE.md",
        "docs/CONNECT.md",
        "docs/DECISIONS.md",
        "docs/EXAMPLE_SESSION.md",
        "docs/LICENSE_AUDIT.md",
        "docs/PLAN_AND_SKETCH.md",
        "docs/PRINCIPLES.md",
        "docs/ROADMAP.md",
        "docs/plugins.md",
        "examples/rod_handler_notes.md",
    }
)

PREFIXES = (
    "docs/decisions/",
    "docs/guides/",
    "docs/reference/",
    "docs/roadmap/",
    "docs/tools/",
)

TEXT_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc", ".asciidoc"})

# "grant" singular is a license-permission word and is not banned.
# "price" is not "pricing".
BANNED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("monetization", re.compile(r"\bmonetizations?\b|\bmonetize\b|\bmonetizing\b", re.I)),
    ("pricing", re.compile(r"\bpricing\b", re.I)),
    ("revenue", re.compile(r"\brevenue\b", re.I)),
    ("investor", re.compile(r"\binvestors?\b", re.I)),
    ("launch plan", re.compile(r"\blaunch plans?\b", re.I)),
    ("showcase", re.compile(r"\bshowcases?\b", re.I)),
    # "grants" is not banned: the AGPL license text uses it, and the
    # license file cannot be edited to add an override.
    ("funding", re.compile(r"\bfunding\b", re.I)),
    ("sponsorship", re.compile(r"\bsponsorships?\b", re.I)),
    ("donation", re.compile(r"\bdonations?\b", re.I)),
)

OVERRIDE = re.compile(r"docs-allow:\s*([^<\n]+)", re.I)
FORBIDDEN_PREFIXES = ("private/", "notes/")


def is_text_doc(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if name in {"LICENSE", "COPYING", "NOTICE"}:
        return True
    return Path(name).suffix.lower() in TEXT_SUFFIXES


def is_allowed(path: str) -> bool:
    if path in EXACT:
        return True
    return any(path.startswith(prefix) for prefix in PREFIXES)


def _overrides(line: str) -> set[str]:
    found: set[str] = set()
    for match in OVERRIDE.finditer(line):
        payload = match.group(1).split("-->", 1)[0].strip().lower()
        if payload in {"*", "all"}:
            found.add("*")
            continue
        for part in re.split(r",|/|\band\b", payload):
            part = part.strip(" .;:-")
            if part:
                found.add(part)
    return found


def keyword_hits(text: str) -> list[tuple[int, str]]:
    """Return (line_number, keyword) hits. Line numbers start at 1."""
    lines = text.splitlines()
    hits: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        allowed = _overrides(line)
        if index > 0:
            allowed |= _overrides(lines[index - 1])
        if "*" in allowed or "all" in allowed:
            continue
        for keyword, pattern in BANNED:
            if keyword in allowed:
                continue
            if pattern.search(line):
                hits.append((index + 1, keyword))
    return hits


def scan_files(items: list[tuple[str, str]]) -> list[str]:
    """Scan (repo-relative path, text) pairs. Text is ignored for non-documents."""
    errors: list[str] = []
    for path, text in items:
        norm = path.replace("\\", "/").lstrip("./")
        if norm.startswith(FORBIDDEN_PREFIXES) or norm in {"private", "notes"}:
            errors.append(f"{norm}: private/ and notes/ are local only and cannot be committed")
            continue
        if not is_text_doc(norm):
            continue
        if not is_allowed(norm):
            errors.append(f"{norm}: text document is not on the software-docs allowlist")
            continue
        for line_no, keyword in keyword_hits(text):
            errors.append(f"{norm}:{line_no}: banned keyword {keyword!r}")
    return errors


def _git(root: Path, args: list[str]) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)


def _paths_from_z(blob: bytes) -> list[str]:
    return [part.decode() for part in blob.split(b"\0") if part]


def collect_paths(root: Path, staged: bool) -> list[str]:
    if staged:
        return _paths_from_z(_git(root, ["diff", "--cached", "--name-only", "-z"]))
    tracked = _paths_from_z(_git(root, ["ls-files", "-z"]))
    others = _paths_from_z(
        _git(
            root,
            [
                "ls-files",
                "-z",
                "--others",
                "--exclude-standard",
                "--",
                "*.md",
                "*.markdown",
                "*.rst",
                "*.txt",
                "*.adoc",
                "*.asciidoc",
                "LICENSE",
                "COPYING",
            ],
        )
    )
    return tracked + others


def read_blob(root: Path, path: str, staged: bool) -> str:
    if staged:
        data = _git(root, ["show", f":{path}"])
        return data.decode("utf-8", errors="replace")
    file = root / path
    if not file.is_file():
        return ""
    return file.read_text(encoding="utf-8", errors="replace")


def scan_repository(root: Path, staged: bool = False) -> list[str]:
    paths = collect_paths(root, staged)
    items: list[tuple[str, str]] = []
    for path in paths:
        if is_text_doc(path) or path.replace("\\", "/").startswith(FORBIDDEN_PREFIXES):
            items.append((path, read_blob(root, path, staged) if is_text_doc(path) else ""))
        elif path in {"private", "notes"}:
            items.append((path, ""))
    return scan_files(items)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="scan the index instead of the worktree")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.root
    if root is None:
        root = Path(__file__).resolve().parents[1]
    errors = scan_repository(root, staged=args.staged)
    if errors:
        print(f"{len(errors)} documentation guard failure(s):", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("documentation guard passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
