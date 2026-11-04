"""The STEP panel and its errors show project paths, not the full folder path."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError

_PANEL_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[2], "utf8");
function escapeHtml(text) {
  return String(text == null ? "" : text).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
const panel = { innerHTML: "" };
const forms = {};
const context = {
  boot() { return Promise.resolve(); },
  showError() {},
  escapeHtml,
  escapeAttr(text) { return escapeHtml(text).replace(/"/g, "&quot;"); },
  ui: { reports: {}, state: { project: { path: process.argv[3], name: process.argv[4] } } },
  $(id) { if (id === "panel") return panel; return forms[id] || (forms[id] = {}); },
};
vm.createContext(context);
vm.runInContext(code, context);
vm.runInContext("renderStepPanel()", context);
process.stdout.write(panel.innerHTML);
"""


def test_the_step_panel_names_the_project_folder_not_its_full_path(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    probe = tmp_path / "probe.js"
    probe.write_text(_PANEL_PROBE, encoding="utf-8")
    full = "/home/someone/projects/bracket"
    html = subprocess.run(
        [node, str(probe), str(Path("src/aicad/app_static/tools.js").resolve()), full, "bracket"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert "<h3>STEP</h3>" in html
    assert full not in html
    assert "project folder bracket" in html
    assert "relative path such as models/part.step" in html


def test_a_missing_step_file_in_the_project_is_named_relative_to_it(tmp_path):
    project = tmp_path / "proj"
    document = Document(project)
    with pytest.raises(GeometryError) as caught:
        document.import_step("bracket", "models/bracket.step")
    message = str(caught.value)
    assert message == "STEP file not found: models/bracket.step"
    assert str(tmp_path) not in message
