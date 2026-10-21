"""Session persistence, export, and rendering."""

from pathlib import Path

import pytest
from PIL import Image

from aicad import Document
from aicad.errors import PartNotFound


def _png_stddev(path: Path) -> float:
    image = Image.open(path).convert("RGB")
    histogram = image.histogram()
    # Mean and variance from the histogram, across all channels.
    count = image.size[0] * image.size[1] * 3
    total = sum(index * count_at for index, count_at in enumerate(histogram))
    mean = total / count
    variance = sum(count_at * (index - mean) ** 2 for index, count_at in enumerate(histogram))
    return (variance / count) ** 0.5


def test_document_round_trip_and_names(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("bracket", "part = Box(8, 4, 2)\n")
    assert [part.name for part in document.list_parts()] == ["bracket"]
    source = document.get_source("bracket")
    assert source["source"] == "script"
    assert "Box" in source["script"]

    with pytest.raises(PartNotFound, match="bracket"):
        document.measure("missing")

    reopened = Document(tmp_path)
    assert reopened.measure("bracket").volume_mm3 == pytest.approx(64)

    reopened.delete_part("bracket")
    assert reopened.list_parts() == []
    assert Document(tmp_path).list_parts() == []


def test_step_round_trip_and_stl(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", "part = Box(10, 20, 30)\n")
    step = document.export("box", tmp_path / "box.step", "step")
    stl = document.export("box", tmp_path / "box.stl", "stl")

    text = step.read_text(encoding="utf-8", errors="replace")
    assert "ISO-10303-21" in text
    assert stl.stat().st_size > 200
    # Binary STL: 80-byte header, then a little-endian triangle count.
    count = int.from_bytes(stl.read_bytes()[80:84], "little")
    assert count > 8

    imported = document.import_step("box_again", step)
    assert imported.volume_mm3 == pytest.approx(6000, rel=1e-6)
    assert imported.watertight is True
    again = Document(tmp_path)
    assert again.measure("box_again").volume_mm3 == pytest.approx(6000, rel=1e-6)


def test_render_writes_distinct_views(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "lug",
        "part = Box(20, 10, 4) + Pos(0, 0, 6) * Cylinder(2, 8)\n",
    )
    views = document.render("lug")
    by_name = {view.name: Path(view.path) for view in views}
    expected = {"front", "back", "left", "right", "top", "bottom", "iso", "contact"}
    assert expected <= set(by_name)
    for name, path in by_name.items():
        assert path.is_file()
        assert path.read_bytes().startswith(b"\x89PNG")
        assert _png_stddev(path) > 8, name
    assert by_name["front"].read_bytes() != by_name["top"].read_bytes()
    assert by_name["front"].read_bytes() != by_name["iso"].read_bytes()
