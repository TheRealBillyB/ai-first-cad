"""Blueprint sheet ids cannot be Windows reserved device names.

The picture stays under plans/ in the project. A reserved id is refused
before a file is written.
"""

from __future__ import annotations

import json

import pytest

from aicad.document import Document
from aicad.errors import PlanError

_RESERVED = ["con", "nul", "aux", "prn", "com1", "lpt1", "com9"]


def test_a_reserved_sheet_id_is_refused(tmp_path):
    project = tmp_path / "proj"
    document = Document(project)
    document.start_plan("bench", "Bench")
    for sheet_id in _RESERVED:
        with pytest.raises(PlanError, match="reserved"):
            document.put_plan_sheet(
                "bench",
                {
                    "id": sheet_id,
                    "title": "Top",
                    "blocks": [{"x": 1, "y": 1, "w": 2, "h": 2, "label": "plate"}],
                },
            )
    document.put_plan_sheet(
        "bench",
        {
            "id": "s1",
            "title": "Top",
            "path": "../outside/sheet.png",
            "blocks": [{"x": 1, "y": 1, "w": 2, "h": 2, "label": "plate"}],
        },
    )
    stored = json.loads((project / "plans" / "bench.json").read_text(encoding="utf-8"))
    sheet = stored["revisions"][0]["sheets"][0]
    assert sheet["id"] == "s1"
    assert (project / sheet["path"]).resolve().is_relative_to(project.resolve())
    assert list(project.rglob("con.png")) == []
    assert not (tmp_path / "outside").exists()

    sheet["id"] = "con"
    (project / "plans" / "bench.json").write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(PlanError, match="reserved"):
        document.get_plan("bench")
    assert list(project.rglob("con.png")) == []
