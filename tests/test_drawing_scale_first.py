"""A bad scale is refused before the solids are posed. That work takes seconds."""

import pytest

from aicad import Document, GeometryError


def test_a_bad_scale_is_refused_before_the_assembly_is_built(tmp_path, monkeypatch):
    document = Document(tmp_path)
    document.create_part_from_ops("base", [{"op": "add", "shape": "box", "size": [10, 10, 4]}])
    document.create_assembly({"name": "unit", "instances": [{"name": "base", "part": "base"}], "joints": []})

    def built(*_args, **_kwargs):
        raise AssertionError("the assembly was built before the scale was read")

    monkeypatch.setattr(Document, "_assembly_shapes", built)
    with pytest.raises(GeometryError, match="ratio"):
        document.export_drawing("unit", scale="banana")
