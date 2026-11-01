"""A constrained rectangle and a bore, extruded into a pad.

    python examples/sketch_pad.py

Writes examples/renders/sketch_pad.png and examples/renders/sketch_pad_contact.png.
Pass --out to write those pictures somewhere else.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from aicad import Sketch
from aicad.file_access import read_file
from aicad.paths import publish_bytes
from aicad.render import render_sketch
from aicad.workspace import Workspace

ROOT = Path(__file__).resolve().parent


def pad_sketch() -> Sketch:
    """50 by 30 mm plate, 8 mm bore, fully constrained."""
    return (
        Sketch()
        .point("origin", 0, 0)
        .fix("origin")
        .point("sw", -25, -15)
        .point("se", 25, -15)
        .point("ne", 25, 15)
        .point("nw", -25, 15)
        .line("bottom", "sw", "se")
        .line("right", "se", "ne")
        .line("top", "ne", "nw")
        .line("left", "nw", "sw")
        .line("diag", "sw", "ne", construction=True)
        .horizontal("bottom")
        .horizontal("top")
        .vertical("left")
        .vertical("right")
        .distance("sw", "se", 50, name="length")
        .distance("sw", "nw", 30, name="width")
        .midpoint("origin", "diag")
        .circle("bore", "origin", 4)
        .diameter("bore", 8, name="bore")
    )


def build(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        workspace = Workspace(Path(folder) / "project")
        solved = workspace.document.create_sketch("pad", pad_sketch())
        if solved.status != "fully_constrained":
            raise RuntimeError(solved.note)
        workspace.document.create_part_from_sketch(
            "pad",
            "pad",
            [
                {"op": "extrude", "points": ["sw", "se", "ne", "nw"], "height": 6, "name": "plate"},
                {"op": "cut", "circle": "bore", "height": 10, "z": -2, "name": "bore"},
            ],
        )
        sketch_path = out / "sketch_pad.png"
        rendered = render_sketch(workspace.document.get_sketch("pad"), sketch_path)
        # render_sketch writes the file. Read it back through the shared reader
        # and replace it, so the checked-in picture is not left as a direct write.
        publish_bytes(sketch_path, read_file(rendered.path, maximum=8_000_000))
        report = workspace.document.inspect("pad")
        contact = next(view for view in report.views if view.name == "contact")
        publish_bytes(out / "sketch_pad_contact.png", read_file(contact.path, maximum=8_000_000))
        return {
            "status": solved.status,
            "dof": solved.dof,
            "volume_mm3": report.measurement.volume_mm3,
            "renders": ["sketch_pad.png", "sketch_pad_contact.png"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the sketch pad example.")
    parser.add_argument("--out", type=Path, default=ROOT / "renders")
    args = parser.parse_args()
    print(json.dumps(build(args.out)))


if __name__ == "__main__":
    main()
