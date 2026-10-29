"""A name Windows stores as an existing file is refused.

The marker file outside the project stays as it was written. Nothing is
sent to another machine.
"""

from __future__ import annotations

import pytest

from aicad.errors import GeometryError
from aicad.paths import require_export_path


def test_a_dotless_i_is_the_same_file_as_i(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text("untouched\n", encoding="utf-8")
    project = tmp_path / "project"
    exports = project / "exports"
    exports.mkdir(parents=True)
    existing = exports / "i.step"
    existing.write_text("kept\n", encoding="utf-8")
    dotless = "exports/\u0131.step"
    with pytest.raises(GeometryError, match="existing name"):
        require_export_path(project, dotless, "Export path", GeometryError)
    assert existing.read_text(encoding="utf-8") == "kept\n"
    assert marker.read_text(encoding="utf-8") == "untouched\n"
    assert not (exports / "\u0131.step").exists()
