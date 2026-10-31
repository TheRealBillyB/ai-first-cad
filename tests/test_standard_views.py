"""Standard solid views match the rendered camera directions."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src" / "aicad" / "app_static" / "index.html"
SCRIPT = ROOT / "src" / "aicad" / "app_static" / "app.js"

_VIEWS = ("front", "back", "top", "bottom", "left", "right", "iso")


def test_the_viewport_offers_the_standard_views():
    page = PAGE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    for name in _VIEWS:
        assert f'data-view="{name}"' in page
        assert f"{name}:" in script
    assert 'id="fit"' in page
    assert "function setStandardView" in script
    assert "function viewShortcut" in script
    assert 'title="Fit (0)"' in page


_DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {
  console,
  document: {
    getElementById() { return { hidden: false, style: {}, classList: { toggle() {} }, addEventListener() {} }; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    createElement() { return { style: {}, classList: { toggle() {} } }; },
  },
  sessionStorage: { getItem() { return null; }, setItem() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { userAgent: "test" },
  location: { origin: "http://127.0.0.1:9", href: "http://127.0.0.1:9/" },
  fetch() { return Promise.reject(new Error("no")); },
  structuredClone(value) { return JSON.parse(JSON.stringify(value)); },
  devicePixelRatio: 1,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
const driver = `
ui.stage = "solid";
ui.page = "model";
let drawn = 0;
function drawSolid() { drawn += 1; }
setStandardView("front");
const front = eyePosition().map((value) => Math.round(value * 1000) / 1000);
const frontUp = cameraUp();
setStandardView("top");
const top = eyePosition();
const topUp = cameraUp();
setStandardView("iso");
const iso = eyePosition();
const shortcuts = ["1", "2", "3", "4", "5", "6", "7", "0"].map((key) => viewShortcut({ key, target: { tagName: "BODY" } }));
const typed = viewShortcut({ key: "1", target: { tagName: "INPUT" } });
const chord = viewShortcut({ key: "1", ctrlKey: true, target: { tagName: "BODY" } });
leaveStandardView();
report = {
  front, frontUp, top, topUp, iso,
  shortcuts,
  typed, chord,
  direction: view.direction,
  drawn,
};
`;
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
process.stdout.write(JSON.stringify(sandbox.report));
"""


def test_standard_views_place_the_camera(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "views.js"
    probe.write_text(_DRIVER, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    distance = 80
    assert report["front"] == [0, -distance, 0]
    assert report["frontUp"] == [0, 0, 1]
    assert report["top"][2] == distance
    assert report["top"][0] == 0 and report["top"][1] == 0
    assert report["topUp"] == [0, 1, 0]
    assert report["iso"][0] > 0
    assert report["iso"][1] < 0
    assert report["iso"][2] > 0
    assert report["shortcuts"] == ["front", "back", "top", "bottom", "left", "right", "iso", "fit"]
    assert report["typed"] is None
    assert report["chord"] is None
    assert report["direction"] is None
    assert report["drawn"] == 3
