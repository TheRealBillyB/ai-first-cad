"""An 8.3 short name with a longer numeric suffix is not a project path.

The marker outside the project is SANDBOX_CANARY.txt containing CANARY_HIT.
"""

from __future__ import annotations

import pytest

from aicad.errors import GeometryError
from aicad.paths import require_export_path

_CANARY_TEXT = "CANARY_HIT\n"


def test_a_longer_short_name_suffix_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    refused = (
        "exports/ABCDE~10.STP",
        "exports/AB~100.TXT",
        "exports/A~999999.STE",
        "exports/PROGR~10/model.step",
    )
    for raw in refused:
        with pytest.raises(GeometryError):
            require_export_path(project, raw, "Export path", GeometryError)
    kept = require_export_path(project, "exports/rev-10.step", "Export path", GeometryError)
    assert kept.name == "rev-10.step"
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert not any(project.rglob("SANDBOX_CANARY.txt"))
