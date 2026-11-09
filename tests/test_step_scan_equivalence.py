"""The faster STEP scans give exactly what the character-by-character scans gave.

Opening a project reads each part's saved STEP file, and two safety scans
walked every character of it in Python: on a 38-part finger rig that was over
2 seconds of the open. The scans now jump between comments, quotes, and
brackets. The references below are the previous scans, kept here unchanged, and
every generated input must give the same result or the same refusal.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from aicad import kernel
from aicad.errors import GeometryError

# The previous scans, as they were.


def _reference_code_and_strings(text: str) -> tuple[str, list[str]]:
    code: list[str] = []
    values: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("unclosed comment")
            code.append(" ")
            index = end + 2
            continue
        if text[index] == "'":
            raw, index = _reference_literal(text, index)
            pieces = [raw]
            while True:
                cursor = index
                while cursor < length and text[cursor] in " \t\r\n":
                    cursor += 1
                while cursor < length and text.startswith("/*", cursor):
                    end = text.find("*/", cursor + 2)
                    if end < 0:
                        raise ValueError("unclosed comment")
                    cursor = end + 2
                    while cursor < length and text[cursor] in " \t\r\n":
                        cursor += 1
                if cursor < length and text[cursor] == "'":
                    nxt, index = _reference_literal(text, cursor)
                    pieces.append(nxt)
                    continue
                index = cursor
                break
            values.append(kernel._decode_step_string("".join(pieces)))
            code.append("''")
            continue
        code.append(text[index])
        index += 1
    return "".join(code), values


def _reference_literal(text: str, index: int) -> tuple[str, int]:
    index += 1
    chars: list[str] = []
    length = len(text)
    while index < length:
        if text[index] == "'":
            if index + 1 < length and text[index + 1] == "'":
                chars.append("'")
                index += 2
                continue
            return "".join(chars), index + 1
        chars.append(text[index])
        index += 1
    raise ValueError("unterminated string")


def _reference_accepted(path: Path, data: bytes) -> bytes:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    start = 0
    while start < len(data) and data[start] in b" \t\r\n":
        start += 1
    if start:
        data = data[start:]
    if not data.startswith(b"ISO-10303-21"):
        raise GeometryError(f"Could not read STEP file {path}.")
    text = data.decode("latin-1")
    depth = 0
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise GeometryError(f"Could not read STEP file {path}.")
            index = end + 2
            continue
        char = text[index]
        if char == "'":
            index += 1
            while index < length:
                if text[index] == "'":
                    if index + 1 < length and text[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise GeometryError(f"Could not read STEP file {path}.")
            continue
        if char == "(":
            depth += 1
            if depth > kernel._MAX_STEP_LIST_DEPTH:
                raise GeometryError(f"Could not read STEP file {path}.")
        elif char == ")" and depth:
            depth -= 1
        index += 1
    return data


# Inputs built from the pieces the scans care about, in every order.
_PIECES = [
    "/*", "*/", "/", "*", "'", "''", "'''", "(", ")", "((", "))", " ", "\t", "\r\n", "\n",
    "a", "#12", "=", ",", ";", "DOCUMENT_FILE", "x\\X2\\00E9\\X0\\", "\\\\", "C:\\x", "../up",
    "'part.step'", "/* note */", "'a' 'b'", "'a' /* c */ 'b'",
]


def _generated(seed: int, count: int) -> list[str]:
    chooser = random.Random(seed)
    return ["".join(chooser.choice(_PIECES) for _ in range(chooser.randint(0, 40))) for _ in range(count)]


def _outcome(function, *args):
    try:
        return ("value", function(*args))
    except (ValueError, GeometryError) as exc:
        return ("refused", type(exc).__name__, str(exc))


@pytest.mark.parametrize("seed", range(6))
def test_the_code_and_string_scan_matches_the_previous_scan(seed):
    for text in _generated(seed, 800):
        assert _outcome(kernel._step_code_and_strings, text) == _outcome(_reference_code_and_strings, text), repr(text)


@pytest.mark.parametrize("seed", range(6))
def test_the_nesting_scan_matches_the_previous_scan(seed):
    path = Path("part.step")
    for body in _generated(100 + seed, 800):
        data = ("ISO-10303-21;" + body).encode("latin-1")
        assert _outcome(kernel._accepted_step_bytes, path, data) == _outcome(_reference_accepted, path, data), repr(body)


def test_the_nesting_cap_and_its_edges_match():
    path = Path("part.step")
    cap = kernel._MAX_STEP_LIST_DEPTH
    for body in (
        "(" * cap + ")" * cap,
        "(" * (cap + 1),
        "'(((' " + "(" * cap,
        "/* " + "(" * (cap + 5) + " */ (",
        ")" * 10 + "(" * cap,
        "'unclosed",
        "/* unclosed",
        "'it''s' (",
    ):
        data = ("\ufeff".encode("utf-8") + b"  ISO-10303-21;" + body.encode("latin-1"))
        assert _outcome(kernel._accepted_step_bytes, path, data) == _outcome(_reference_accepted, path, data), body


def test_literals_joined_across_whitespace_and_comments_match():
    for text in (
        "#1=DOCUMENT_FILE('a' 'b' /* x */ 'c',$);",
        "'a''b''' 'c'",
        "'x' /* unclosed",
        "'' ''",
        "'a'\r\n\t/*1*//*2*/ 'b' ;",
    ):
        assert _outcome(kernel._step_code_and_strings, text) == _outcome(_reference_code_and_strings, text), text
