"""Import a closed STL block and measure it with inspect.

    python examples/mesh_block.py

Writes examples/renders/mesh_block_contact.png. The same run refreshes
examples/meshes/block.stl. Pass --out to write only the picture.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from aicad.file_access import read_file
from aicad.paths import publish_bytes
from aicad.workspace import Workspace

ROOT = Path(__file__).resolve().parent

# A 40 x 24 x 16 mm box. Twelve triangles, one closed shell.
_BLOCK_STL = """solid block
  facet normal 0 0 -1
    outer loop
      vertex 0 0 0
      vertex 40 24 0
      vertex 40 0 0
    endloop
  endfacet
  facet normal 0 0 -1
    outer loop
      vertex 0 0 0
      vertex 0 24 0
      vertex 40 24 0
    endloop
  endfacet
  facet normal 0 0 1
    outer loop
      vertex 0 0 16
      vertex 40 0 16
      vertex 40 24 16
    endloop
  endfacet
  facet normal 0 0 1
    outer loop
      vertex 0 0 16
      vertex 40 24 16
      vertex 0 24 16
    endloop
  endfacet
  facet normal 0 -1 0
    outer loop
      vertex 0 0 0
      vertex 40 0 0
      vertex 40 0 16
    endloop
  endfacet
  facet normal 0 -1 0
    outer loop
      vertex 0 0 0
      vertex 40 0 16
      vertex 0 0 16
    endloop
  endfacet
  facet normal 0 1 0
    outer loop
      vertex 0 24 0
      vertex 40 24 16
      vertex 40 24 0
    endloop
  endfacet
  facet normal 0 1 0
    outer loop
      vertex 0 24 0
      vertex 0 24 16
      vertex 40 24 16
    endloop
  endfacet
  facet normal -1 0 0
    outer loop
      vertex 0 0 0
      vertex 0 0 16
      vertex 0 24 16
    endloop
  endfacet
  facet normal -1 0 0
    outer loop
      vertex 0 0 0
      vertex 0 24 16
      vertex 0 24 0
    endloop
  endfacet
  facet normal 1 0 0
    outer loop
      vertex 40 0 0
      vertex 40 24 0
      vertex 40 24 16
    endloop
  endfacet
  facet normal 1 0 0
    outer loop
      vertex 40 0 0
      vertex 40 24 16
      vertex 40 0 16
    endloop
  endfacet
endsolid block
"""


def build(out: Path, *, write_mesh: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    stl = _BLOCK_STL.encode("utf-8")
    if write_mesh:
        mesh_dir = ROOT / "meshes"
        mesh_dir.mkdir(parents=True, exist_ok=True)
        publish_bytes(mesh_dir / "block.stl", stl)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "project"
        workspace = Workspace(root)
        publish_bytes(root / "block.stl", stl, root=root)
        workspace.document.import_stl("block", "block.stl")
        report = workspace.document.inspect("block", material="pla")
        contact = next(view for view in report.views if view.name == "contact")
        publish_bytes(out / "mesh_block_contact.png", read_file(contact.path, maximum=8_000_000))
        measurement = report.measurement
        center = measurement.center_of_mass_mm
        return {
            "source": "mesh",
            "volume_mm3": measurement.volume_mm3,
            "area_mm2": measurement.area_mm2,
            "center_of_mass_mm": [center.x, center.y, center.z],
            "mass_g": measurement.mass_g,
            "render": "mesh_block_contact.png",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the block STL and inspect it.")
    parser.add_argument("--out", type=Path, default=ROOT / "renders")
    args = parser.parse_args()
    default_out = (ROOT / "renders").resolve()
    print(json.dumps(build(args.out, write_mesh=args.out.resolve() == default_out)))


if __name__ == "__main__":
    main()
