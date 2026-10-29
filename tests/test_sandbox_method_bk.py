"""A part name in a PDF drawing stays inside one text string.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT. Parentheses, a
backslash, and a newline in the name do not add a PDF command, and that
file is not changed.
"""

from __future__ import annotations

from pathlib import Path

from aicad.drawing import _Text, _write_pdf

_MARKER = "SANDBOX_CANARY.txt"
_TEXT = "CANARY_HIT\n"


def _sheet(path: Path, text: str) -> None:
    _write_pdf(path, [], [_Text(10, 10, text, 3.0, "TITLE", (0, 0, 0))], [])


def test_a_part_name_stays_inside_the_pdf_string(tmp_path):
    marker = tmp_path / _MARKER
    marker.write_text(_TEXT, encoding="utf-8")
    plain = tmp_path / "plain.pdf"
    nasty = tmp_path / "nasty.pdf"
    _sheet(plain, "bracket")
    _sheet(nasty, "part) Tj\n100 0 Td (CANARY_HIT) Tj\n(\\")
    plain_bytes = plain.read_bytes()
    nasty_bytes = nasty.read_bytes()
    assert plain_bytes.count(b"endobj") == nasty_bytes.count(b"endobj")
    assert plain_bytes.count(b"%%EOF") == 1
    assert nasty_bytes.count(b"%%EOF") == 1
    assert b"(CANARY_HIT) Tj" not in nasty_bytes
    assert b"\\(CANARY_HIT\\)" in nasty_bytes
    assert marker.read_text(encoding="utf-8") == _TEXT
    assert {path.name for path in tmp_path.iterdir()} == {_MARKER, "plain.pdf", "nasty.pdf"}
