"""Project text in the workspace is shown as text.

The canary is an image address, CANARY_HIT, written into names and notes.
Opening the project renders those strings. The rendered page has no element
for that address.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

CANARY = '"><img src="http://127.0.0.1/CANARY_HIT">'

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const marker = process.argv[3];
const elements = {};
function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id,
      hidden: false,
      innerHTML: "",
      textContent: "",
      title: "",
      className: "",
      src: "",
      value: "",
      disabled: false,
      dataset: {},
      style: {},
      classList: { toggle() {} },
      querySelectorAll() { return []; },
      setAttribute() {},
      removeAttribute() {},
      addEventListener() {},
      append() {},
      replaceChildren() {},
    };
  }
  return elements[id];
}
const document = {
  getElementById: element,
  querySelector() { return { getAttribute() { return ""; } }; },
  createElement() { return element("created"); },
  documentElement: { style: { setProperty() {} } },
};
const context = {
  document,
  sessionStorage: { getItem() { return "off"; } },
  console,
  alert() {},
  requestAnimationFrame() {},
  fetch() { return Promise.resolve({ json() { return Promise.resolve({ ok: true, result: {} }); } }); },
  Headers: class Headers { constructor() {} set() {} },
};
context.marker = marker;
vm.createContext(context);
const driver = `
ui.part = marker;
ui.assembly = marker;
ui.sketch = { dimensions: [{ name: marker, value: marker, kind: "distance" }] };
ui.state = {
  revision: 1,
  parts: [{ name: marker, bounding_box: { size: { x: 1, y: 2, z: 3 } } }],
  sketches: [{ name: marker, status: "under_constrained" }],
  panels: {
    features: { tracked: true, steps: [{ name: marker, op: marker, redundant: false }] },
    section: { area_mm2: 1, min_thickness_mm: 1, note: marker, view: null },
    motion: {
      assembly: marker,
      motion: marker,
      frames: [{ time_s: 0, collisions: [{ a: marker, b: marker }] }],
      views: [],
    },
  },
  panel_revision: { features: 1, inspect: 1, section: 1 },
  materials: { materials: [{ id: marker, name: marker }] },
  documents: [marker],
  requirements: [],
  assemblies: [{
    name: marker,
    instances: [{ name: marker, part: "base" }],
    joints: [{ name: marker, type: "revolute", value: 0, min: null, max: null }],
    poses: [marker],
    motions: [marker],
    studies: [],
  }],
  project: { name: "proj", path: "/tmp/proj" },
  can_undo: false,
  can_redo: false,
  session_record: {},
  design_scripts: { enabled: false, warning: marker },
};
renderTree();
renderPanel();
ui.tab = "section";
renderPanel();
ui.tab = "fea";
renderPanel();
document.getElementById("page-docs").hidden = false;
renderDocs();
renderAssemblies();
renderDimensions();
showMotionReport();
`;
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + driver, context, { filename: "app.js" });
const html = Object.values(elements).map((node) => node.innerHTML).join("\n");
const tags = html.match(/<\/?[a-zA-Z][^>]*>/g) || [];
process.stdout.write(JSON.stringify({
  html,
  img: tags.filter((tag) => /^<img\b/i.test(tag)),
  script: tags.filter((tag) => /^<script\b/i.test(tag)),
}));
"""


def test_opening_the_workspace_does_not_treat_project_text_as_markup(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "render.js"
    probe.write_text(_PROBE, encoding="utf-8")
    app = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static" / "app.js"
    result = subprocess.run(
        [node, str(probe), str(app), CANARY],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["img"] == []
    assert report["script"] == []
    assert "CANARY_HIT" in report["html"]
    assert "&lt;img" in report["html"]
    assert "<img" not in report["html"].lower()
