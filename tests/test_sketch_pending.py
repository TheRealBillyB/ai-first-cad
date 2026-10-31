"""The sketch line tool keeps its first point when the view refreshes."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEOM = ROOT / "src" / "aicad" / "app_static" / "sketch_geom.js"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "sketch_geom.js" });

const local = {
  spec: {
    entities: [
      { id: "origin", type: "point", at: [0, 0] },
      { id: "p1", type: "point", at: [12.5, 4] },
    ],
  },
  geometry: { points: { origin: { x: 0, y: 0 }, p1: { x: 12.5, y: 4 } } },
};
const server = {
  spec: { plane: "xy", entities: [{ id: "origin", type: "point", at: [0, 0] }], constraints: [] },
  geometry: { points: { origin: { x: 0, y: 0 } } },
};
const pending = sandbox.sketchPendingPoint(local, "p1");
const kept = sandbox.sketchWithPendingPoint(server, pending);
const again = sandbox.sketchWithPendingPoint(kept, pending);
const missing = sandbox.sketchPendingPoint(server, "p1");
const cleared = sandbox.sketchWithPendingPoint(server, null);
const ids = kept.spec.entities.map((entity) => entity.id);
const center = sandbox.sketchPendingPoint({
  spec: { entities: [{ id: "c0", type: "point", at: [5, 1] }] },
}, "c0");
const withCenter = sandbox.sketchWithPendingPoint(kept, center);
process.stdout.write(JSON.stringify({
  pending,
  ids,
  at: kept.spec.entities.find((entity) => entity.id === "p1").at,
  drawn: kept.geometry.points.p1,
  count: kept.spec.entities.length,
  again: again.spec.entities.length,
  missing,
  cleared: cleared === server,
  centerAt: withCenter.spec.entities.find((entity) => entity.id === "c0").at,
  centerDrawn: withCenter.geometry.points.c0,
  stillHasLine: withCenter.spec.entities.some((entity) => entity.id === "p1"),
}));
"""


def test_a_refresh_keeps_the_line_tools_first_point(tmp_path):
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert "sketchPendingPoint(ui.sketch, ui.lineStart)" in script
    assert "sketchWithPendingPoint" in script
    node = shutil.which("node")
    assert node
    probe = tmp_path / "sketch-pending.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(GEOM)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["pending"] == {"id": "p1", "at": [12.5, 4]}
    assert report["ids"] == ["origin", "p1"]
    assert report["at"] == [12.5, 4]
    assert report["drawn"] == {"x": 12.5, "y": 4}
    assert report["count"] == 2
    assert report["again"] == 2
    assert report["missing"] is None
    assert report["cleared"] is True
    assert report["centerAt"] == [5, 1]
    assert report["centerDrawn"] == {"x": 5, "y": 1}
    assert report["stillHasLine"] is True
