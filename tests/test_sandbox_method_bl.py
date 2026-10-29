"""A part name in a DXF drawing stays in one group.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT. A newline in the
name does not start another entity, and that file is not changed.
"""

from __future__ import annotations

from aicad.drawing import _Text, _write_dxf

_MARKER = "SANDBOX_CANARY.txt"
_TEXT = "CANARY_HIT\n"


def _sheet(path, text: str) -> None:
    _write_dxf(path, [], [_Text(10, 10, text, 3.0, "TITLE", (0, 0, 0))])


def test_a_part_name_stays_in_one_dxf_group(tmp_path):
    marker = tmp_path / _MARKER
    marker.write_text(_TEXT, encoding="utf-8")
    plain = tmp_path / "plain.dxf"
    nasty = tmp_path / "nasty.dxf"
    _sheet(plain, "bracket")
    _sheet(nasty, "part\n0\nLINE\nCANARY_HIT\r\\")
    plain_lines = plain.read_text(encoding="utf-8").splitlines()
    nasty_lines = nasty.read_text(encoding="utf-8").splitlines()
    assert len(plain_lines) == len(nasty_lines)
    assert plain_lines.count("TEXT") == 1
    assert nasty_lines.count("TEXT") == 1
    assert nasty_lines.count("LINE") == plain_lines.count("LINE")
    value = next(line for line in nasty_lines if "CANARY_HIT" in line)
    assert value == "part\\n0\\nLINE\\nCANARY_HIT\\n\\\\"
    assert marker.read_text(encoding="utf-8") == _TEXT
    assert {path.name for path in tmp_path.iterdir()} == {_MARKER, "plain.dxf", "nasty.dxf"}
