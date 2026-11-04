"""A tool description names the keys a call has to send."""

import asyncio

from aicad.fea import select_faces
from aicad.server import build_server


def _descriptions(tmp_path) -> dict:
    return {tool.name: tool.description or "" for tool in asyncio.run(build_server(tmp_path).list_tools())}


def test_sketch_steps_name_the_op_key(tmp_path):
    assert "`op`" in _descriptions(tmp_path)["create_part_from_sketch"]


def test_every_selector_kind_is_described_and_accepted(tmp_path):
    described = _descriptions(tmp_path)["solve_mechanics"]
    catalog = [{"index": 0, "kind": "plane", "normal": [0, 0, 1], "center_mm": [0, 0, 5]}]
    selectors = [
        {"kind": "plane", "normal": [0, 0, 1]},
        {"kind": "nearest", "at": [0, 0, 5]},
        {"kind": "index", "index": 0},
    ]
    for selector in selectors:
        assert f'"kind": "{selector["kind"]}"' in described
        assert select_faces(catalog, selector) == catalog
    assert '"kind": "cylinder"' in described
    assert '"ref"' in described
