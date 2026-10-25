"""A bill of materials keeps a route name and a material as text.

A plain part name is written unchanged. The outside project is not used.
"""

from __future__ import annotations

import csv
from pathlib import Path

from aicad.bom import write_csv
from aicad.document import Document


def test_write_csv_keeps_text_cells_as_text(tmp_path):
    path = tmp_path / "bom.csv"
    write_csv(
        {
            "rows": [
                {
                    "item": 1,
                    "quantity": 1,
                    "category": "hose",
                    "name": "=supply",
                    "description": "+note",
                    "material": " @oak",
                    "note": "-see",
                    "mass_estimate_kg": -1.5,
                    "instances": ["plate"],
                },
                {
                    "item": 2,
                    "quantity": 1,
                    "category": "part",
                    "name": "plate",
                    "description": "Modeled part plate.",
                    "material": "steel",
                    "instances": ["plate"],
                },
                {
                    "item": 3,
                    "quantity": 1,
                    "category": "hose",
                    "name": "供水",
                    "material": "rubber",
                    "instances": ["部件"],
                },
            ]
        },
        path,
    )
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["name"] == "'=supply"
    assert rows[0]["description"] == "'+note"
    assert rows[0]["material"] == "' @oak"
    assert rows[0]["note"] == "'-see"
    assert rows[0]["mass_estimate_kg"] == "-1.5"
    assert rows[0]["instances"] == "plate"
    assert rows[1]["name"] == "plate"
    assert rows[1]["material"] == "steel"
    assert rows[2]["name"] == "供水"
    assert rows[2]["instances"] == "部件"


def test_a_route_name_and_a_material_stay_text_in_the_project_file(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops(
        "plate",
        [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 0]}],
    )
    document.create_assembly(
        {
            "name": "kit",
            "instances": [{"name": "plate", "part": "plate", "material": "=oak"}],
            "cables": [
                {
                    "name": "=supply",
                    "kind": "hose",
                    "waypoints": [
                        {"instance": "plate", "at": [0, 0, 0]},
                        {"instance": "plate", "at": [4, 0, 0]},
                    ],
                }
            ],
        }
    )
    report = document.bill_of_materials("kit")
    assert any(row["name"] == "=supply" for row in report["rows"])
    with Path(report["csv"]).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    hose = next(row for row in rows if row["category"] == "hose")
    part = next(row for row in rows if row["category"] == "part")
    assert hose["name"] == "'=supply"
    assert part["material"] == "'=oak"
    assert part["name"] == "plate"
    assert tmp_path in Path(report["csv"]).parents
