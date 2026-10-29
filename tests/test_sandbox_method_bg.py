"""A bill-of-materials cell that looks like a formula is stored as text.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT. Writing the table
does not change that file.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from aicad.bom import write_csv

_MARKER = "SANDBOX_CANARY.txt"
_TEXT = "CANARY_HIT\n"


def test_a_formula_cell_is_stored_as_text(tmp_path: Path):
    marker = tmp_path / _MARKER
    marker.write_text(_TEXT, encoding="utf-8")
    path = tmp_path / "project" / "bom.csv"
    path.parent.mkdir()
    write_csv(
        {
            "rows": [
                {
                    "item": "bracket",
                    "name": "=1+1",
                    "description": "\n=cmd",
                    "part": "+1",
                    "note": "-2",
                    "material": "@SUM(A1)",
                    "size": "\t=1",
                }
            ]
        },
        path,
    )
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 1
    for key in ("name", "description", "part", "note", "material", "size"):
        assert rows[0][key].startswith("'")
    assert rows[0]["item"] == "bracket"
    assert rows[0]["name"] == "'=1+1"
    assert marker.read_text(encoding="utf-8") == _TEXT
    assert {item.name for item in tmp_path.rglob("*") if item.is_file()} == {_MARKER, "bom.csv"}
