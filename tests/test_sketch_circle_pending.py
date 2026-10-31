"""The sketch circle tool keeps its center when the view refreshes."""

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

const blank = {
  spec: { plane: "xy", entities: [{ id: "origin", type: "point", at: [0, 0] }], constraints: [] },
  geometry: { points: { origin: { x: 0, y: 0 } } },
};
const started = sandbox.sketchCircleClick(blank.spec, blank.geometry, null, 20, 6, 2);
const server = {
  spec: { plane: "xy", entities: [{ id: "origin", type: "point", at: [0, 0] }], constraints: [] },
  geometry: { points: { origin: { x: 0, y: 0 } } },
};
const pending = sandbox.sketchPendingPoint({ spec: started.spec }, started.circleCenter);
const kept = sandbox.sketchWithPendingPoint(server, pending);
const again = sandbox.sketchWithPendingPoint(kept, pending);
const drawn = sandbox.sketchCircleClick(kept.spec, kept.geometry, started.circleCenter, 20, 16, 2);
const circle = drawn.spec.entities.find((entity) => entity.type === "circle");
const lost = sandbox.sketchCircleClick(server.spec, server.geometry, started.circleCenter, 20, 16, 2);
const lostCircle = lost.spec.entities.some((entity) => entity.type === "circle");
process.stdout.write(JSON.stringify({
  pending,
  center: started.circleCenter,
  commit: started.commit,
  kept: kept.geometry.points[started.circleCenter],
  count: kept.spec.entities.length,
  again: again.spec.entities.length,
  circle,
  finished: drawn.finished,
  cleared: drawn.circleCenter,
  lostFinished: lost.finished,
  lostCircle,
}));
"""


def test_a_refresh_keeps_the_circle_tools_center(tmp_path):
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert "sketchPendingPoint(ui.sketch, ui.circleCenter)" in script
    assert "sketchWithPendingPoint(sketchWithPendingPoint(found, pendingLine), pendingCenter)" in script
    node = shutil.which("node")
    assert node
    probe = tmp_path / "circle-pending.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(GEOM)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["commit"] is True
    assert report["pending"] == {"id": report["center"], "at": [20, 6]}
    assert report["kept"] == {"x": 20, "y": 6}
    assert report["count"] == 2
    assert report["again"] == 2
    assert report["finished"] is True
    assert report["cleared"] is None
    assert report["circle"]["center"] == report["center"]
    assert report["circle"]["radius"] == 10
    assert report["lostFinished"] is False
    assert report["lostCircle"] is False
