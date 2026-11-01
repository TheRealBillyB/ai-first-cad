"""A flexible printed material in the stress library."""

import pytest

from aicad.document import Document
from aicad.fea import list_materials, lookup_material


def test_tpu_is_in_the_library_as_a_printed_flexible_material():
    ids = {row["id"] for row in list_materials()["materials"]}
    assert "tpu_95a" in ids
    for name in ("TPU", "tpu 95a", "tpu_95a"):
        assert lookup_material(name)["id"] == "tpu_95a"
    tpu = lookup_material("tpu")
    assert tpu["printed"] is True
    assert tpu["youngs_modulus_mpa"] == 26.0
    assert tpu["density_g_cm3"] == 1.22
    assert tpu["youngs_modulus_mpa"] < lookup_material("nylon")["youngs_modulus_mpa"] / 10
    assert "small-strain" in tpu["note"]


def test_a_tpu_part_is_weighed_with_the_tpu_density(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_ops("pad", [{"op": "add", "shape": "box", "size": [10, 10, 10], "center": [0, 0, 0]}])
    measured = document.measure("pad", "tpu").to_dict()
    assert measured["mass_g"] == pytest.approx(1.22, rel=1e-3)
