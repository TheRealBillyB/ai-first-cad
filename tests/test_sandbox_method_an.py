"""A normalized name is not a second file.

The marker outside the project is SANDBOX_CANARY.txt containing CANARY_HIT.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.paths import require_export_path

_CANARY_TEXT = "CANARY_HIT\n"


def _pair() -> tuple[str, str]:
    nfc = "caf\u00e9.step"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    return nfc, nfd


def test_a_normalized_link_is_not_a_different_file(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    exports = project / "exports"
    exports.mkdir(parents=True)
    nfc, nfd = _pair()
    link = exports / nfd
    link.symlink_to(canary)
    with pytest.raises(GeometryError):
        require_export_path(project, f"exports/{nfc}", "Export path", GeometryError)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert not (exports / nfc).is_file() or (exports / nfc).samefile(link)


def test_a_normalized_file_is_not_replaced_by_the_other_spelling(tmp_path):
    project = tmp_path / "project"
    exports = project / "exports"
    exports.mkdir(parents=True)
    nfc, nfd = _pair()
    stored = exports / nfd
    stored.write_text("kept\n", encoding="utf-8")
    listed = {entry.name for entry in exports.iterdir()}
    if nfc in listed or nfd not in listed:
        pytest.skip("this filesystem stores both spellings as one name")
    with pytest.raises(GeometryError, match="existing name"):
        require_export_path(project, f"exports/{nfc}", "Export path", GeometryError)
    assert stored.read_text(encoding="utf-8") == "kept\n"
    assert not (exports / nfc).exists()
