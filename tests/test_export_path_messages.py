"""A refused export path says why and names a path that would work."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.paths import require_export_path


def _refusal(root: Path, raw: str) -> str:
    with pytest.raises(GeometryError) as caught:
        require_export_path(root, raw, "Export path", GeometryError)
    return str(caught.value)


def test_a_parent_path_names_where_it_resolves_and_a_path_that_works(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    message = _refusal(root, "../part.step")
    assert f"resolves to {(tmp_path / 'part.step').resolve()}" in message
    assert f"outside the project folder {root.resolve()}" in message
    assert "such as exports/part.step" in message


def test_an_absolute_path_outside_the_project_is_not_said_to_resolve(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = (tmp_path / "part.step").resolve()
    message = _refusal(root, str(outside))
    assert message.startswith(f"Export path {outside} is outside the project folder")
    assert "resolves to" not in message


def test_an_empty_path_is_not_called_outside_the_project(tmp_path):
    message = _refusal(tmp_path, "   ")
    assert message.startswith("Export path is empty")
    assert "outside" not in message
    assert "exports/<file name>" in message


def test_a_lookalike_path_names_the_characters(tmp_path):
    message = _refusal(tmp_path, "exports/part\u200b.step")
    assert "hidden or lookalike characters" in message
    assert "exports/<file name>" in message


def test_a_backslash_escape_names_the_windows_form(tmp_path):
    message = _refusal(tmp_path / "proj", "folder\\..\\..\\part.step")
    assert "Windows drive, share, or backslash path" in message
    assert "such as exports/part.step" in message


def test_a_path_inside_the_project_is_still_returned(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert require_export_path(root, "exports/part.step", "Export path", GeometryError) == (
        root.resolve() / "exports" / "part.step"
    )
