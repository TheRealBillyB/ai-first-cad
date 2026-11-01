"""A small enclosure: a box, four mounting holes, a fillet, and a chamfer.

    python examples/enclosure.py

The contact sheet is written to examples/renders/enclosure_contact.png.
Pass --out to write that picture somewhere else.
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


def _ops() -> list[dict]:
    holes = []
    for name, x, y in (
        ("hole_sw", -22.0, -12.0),
        ("hole_se", 22.0, -12.0),
        ("hole_ne", 22.0, 12.0),
        ("hole_nw", -22.0, 12.0),
    ):
        holes.append(
            {
                "op": "cut",
                "shape": "cylinder",
                "radius": 2.2,
                "height": 24,
                "center": [x, y, 10],
                "name": name,
            }
        )
    return [
        {"op": "add", "shape": "box", "size": [60, 40, 20], "center": [0, 0, 10], "name": "body"},
        *holes,
    ]


def _line(edges: list[dict], *, length: float, x: float | None = None, y: float | None = None) -> dict:
    """One straight edge. The reported point can sit at an end of a straight line."""
    for edge in edges:
        if edge.get("kind") != "line" or abs(edge["length_mm"] - length) > 0.6:
            continue
        mx, my, _mz = edge["midpoint_mm"]
        if x is not None and abs(abs(mx) - abs(x)) > 0.6:
            continue
        if y is not None and abs(abs(my) - abs(y)) > 0.6:
            continue
        return edge
    raise RuntimeError(f"No line of length {length} near x={x}, y={y}.")


def build(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        workspace = Workspace(Path(folder) / "project")
        workspace.execute("create_part_from_ops", {"name": "enclosure", "ops": _ops()})
        edges = workspace.execute("describe_edges", {"name": "enclosure"})["edges"]
        corner = _line(edges, length=20, x=30, y=20)
        workspace.execute(
            "add_fillet",
            {"name": "enclosure", "radius": 3, "edges": [corner["ref"]], "step_name": "corner"},
        )
        edges = workspace.execute("describe_edges", {"name": "enclosure"})["edges"]
        lip = _line(edges, length=60, y=-20)
        workspace.execute(
            "add_chamfer",
            {"name": "enclosure", "distance": 1, "edges": [lip["ref"]], "step_name": "lip"},
        )
        report = workspace.document.inspect("enclosure")
        contact = next(view for view in report.views if view.name == "contact")
        destination = out / "enclosure_contact.png"
        publish_bytes(destination, read_file(contact.path, maximum=8_000_000))
        measurement = report.measurement
        return {
            "volume_mm3": measurement.volume_mm3,
            "watertight": measurement.watertight,
            "render": destination.name,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the enclosure example.")
    parser.add_argument("--out", type=Path, default=ROOT / "renders")
    args = parser.parse_args()
    print(json.dumps(build(args.out)))


if __name__ == "__main__":
    main()
