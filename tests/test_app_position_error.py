"""The position-error button calls check_position_error.

The page posts to /api/command. The two-link arm is the same chain the
command tests use: a 100 mm first link and a tool 80 mm out on the second.
"""

import json
import math
import shutil
import subprocess
import threading
from http.client import HTTPConnection

import pytest

from aicad.app import make_server

_FORM_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[2], "utf8");
function escapeHtml(text) {
  return String(text == null ? "" : text).replace(/[&<>]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[char]));
}
function escapeAttr(text) {
  return escapeHtml(text).replace(/"/g, "&quot;");
}
const context = {
  boot() { return Promise.resolve(); },
  showError() {},
  escapeHtml,
  escapeAttr,
};
vm.createContext(context);
vm.runInContext(code, context);

function channel(values) {
  const map = {
    "[data-error-encoder]": { value: values.encoder == null ? "" : String(values.encoder) },
    "[data-error-backlash]": { value: values.backlash == null ? "" : String(values.backlash) },
    "[data-error-load]": { value: values.load == null ? "" : String(values.load) },
    "[data-error-rate]": { value: values.rate == null ? "" : String(values.rate) },
  };
  return { querySelector(sel) { return Object.prototype.hasOwnProperty.call(map, sel) ? map[sel] : null; } };
}

function row(unit, values) {
  if (unit === "cylindrical") {
    return {
      dataset: { errorUnit: "cylindrical" },
      querySelector(sel) {
        if (sel === "[data-error-angle]") return channel(values.angle || {});
        if (sel === "[data-error-slide]") return channel(values.slide || {});
        return null;
      },
    };
  }
  const root = channel(values);
  root.dataset = { errorUnit: unit };
  return root;
}

function section(html, name) {
  const marker = `data-error-joint="${name}"`;
  const start = html.indexOf(marker);
  const next = html.indexOf("data-error-joint=", start + marker.length);
  return html.slice(start, next === -1 ? html.length : next);
}

const html = context.assemblyFormHtml("error", {
  instances: [{ name: "tip" }],
  joints: [
    { name: "hinge", type: "revolute" },
    { name: "rail", type: "prismatic" },
    { name: "spin", type: "cylindrical" },
  ],
  poses: [],
});

function message(unit, values) {
  try {
    context.positionErrorJointSource(row(unit, values));
  } catch (error) {
    return error.message;
  }
  return null;
}

const report = {
  help: html,
  hinge: section(html, "hinge"),
  rail: section(html, "rail"),
  spin: section(html, "spin"),
  prismatic: context.positionErrorJointSource(row("mm", {
    encoder: "0.02", backlash: "0.2", load: "10", rate: "100",
  })),
  revolute: context.positionErrorJointSource(row("deg", {
    encoder: "0.2", load: "12", rate: "400",
  })),
  cylindrical: context.positionErrorJointSource(row("cylindrical", {
    angle: { encoder: "0.2" },
    slide: { backlash: "0.4" },
  })),
  empty: context.positionErrorJointSource(row("mm", {})),
  forceOnly: message("mm", { load: "10" }),
  torqueOnly: message("deg", { load: "12" }),
};
process.stdout.write(JSON.stringify(report));
"""


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    running = make_server(workspace, "127.0.0.1", 0)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


def _exchange(server, method, path, body=None, token="page", origin=True):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=120)
    headers = {}
    if origin is True:
        headers["Origin"] = f"http://127.0.0.1:{port}"
    elif origin:
        headers["Origin"] = origin
    if token == "page":
        headers["X-Aicad-Token"] = server.page_token
    elif token:
        headers["X-Aicad-Token"] = token
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    connection.close()
    return status, payload


def _command(server, name, arguments):
    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": name, "arguments": arguments}),
    )
    body = json.loads(payload.decode("utf-8"))
    assert status == 200, body
    assert body["ok"] is True
    assert "Traceback" not in payload.decode("utf-8")
    return body["result"]


def _refused(server, name, arguments):
    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": name, "arguments": arguments}),
    )
    text = payload.decode("utf-8")
    body = json.loads(text)
    assert status == 400, body
    assert body["ok"] is False
    assert body["error"]
    assert "Traceback" not in text
    return body["error"]


def _state(server):
    status, payload = _exchange(server, "GET", "/api/state")
    body = json.loads(payload.decode("utf-8"))
    assert status == 200
    assert body["ok"] is True
    return body["result"]


def _arm(server):
    for name in ("base", "link1", "link2"):
        _command(
            server,
            "create_part_from_ops",
            {"name": name, "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]},
        )
    _command(
        server,
        "create_assembly",
        {
            "spec": {
                "name": "arm",
                "instances": [
                    {"name": "base", "part": "base"},
                    {"name": "link1", "part": "link1"},
                    {"name": "link2", "part": "link2"},
                ],
                "joints": [
                    {
                        "name": "shoulder",
                        "type": "revolute",
                        "parent": "base",
                        "child": "link1",
                        "axis": [0, 0, 1],
                    },
                    {
                        "name": "elbow",
                        "type": "revolute",
                        "parent": "link1",
                        "child": "link2",
                        "axis": [0, 0, 1],
                        "parent_anchor": [100, 0, 0],
                    },
                ],
                "poses": {
                    "stretch": {"shoulder": 0, "elbow": 0},
                    "bent": {"shoulder": 0, "elbow": 90},
                },
            },
        },
    )


def _budget(tolerance):
    return {
        "name": "arm",
        "tool": "link2",
        "at": [80, 0, 0],
        "tolerance_mm": tolerance,
        "poses": ["stretch", "bent"],
        "sources": {
            "shoulder": {"encoder": {"resolution_deg": 0.2}},
            "elbow": {"backlash": {"deg": 0.5}},
        },
    }


def test_the_page_wires_position_error_to_the_existing_command(server):
    status, page = _exchange(server, "GET", "/", token=None, origin=None)
    html = page.decode("utf-8")
    assert status == 200
    assert 'id="assembly-error"' in html
    assert "Select an assembly first." not in html

    status, script = _exchange(server, "GET", "/static/tools.js", token=None, origin=None)
    tools = script.decode("utf-8")
    assert status == 200
    assert 'id="assembly-error"' not in tools
    assert 'command("check_position_error"' in tools
    assert "Enter the tolerance in millimeters." in tools
    assert "http://" not in tools
    assert "https://" not in tools


def test_position_error_shows_in_the_panel_and_a_later_edit_clears_it(server):
    _arm(server)
    shoulder = 0.1 * math.pi
    elbow = 0.5 * 80.0 * math.pi / 180.0
    stretch_worst = shoulder + elbow

    report = _command(server, "check_position_error", _budget(0.9))
    assert report["status"] == "fail"
    assert report["worst_pose"] == "stretch"
    assert report["rss_peak_pose"] == "stretch"
    assert report["rss_mm"] < 0.9 < report["worst_case_mm"]
    assert report["worst_case_mm"] == pytest.approx(stretch_worst, abs=1e-6)
    assert report["assembly"] == "arm"
    panel = _state(server)["panels"]["position_error"]
    assert panel["assembly"] == "arm"
    assert panel["status"] == "fail"
    assert panel["worst_pose"] == "stretch"

    passing = _command(server, "check_position_error", _budget(2.0))
    assert passing["status"] == "pass"
    assert passing["worst_case_mm"] == pytest.approx(stretch_worst, abs=1e-6)
    assert "within" in passing["summary"]
    assert _state(server)["panels"]["position_error"]["status"] == "pass"

    error = _refused(
        server,
        "check_position_error",
        {"name": "arm", "tool": "link2", "tolerance_mm": 1.0},
    )
    assert "encoder" in error.lower()
    assert _state(server)["panels"]["position_error"]["status"] == "pass"

    status, payload = _exchange(
        server,
        "POST",
        "/api/command",
        body=json.dumps({"name": "check_position_error", "arguments": _budget(0.9)}),
        token=None,
    )
    assert status == 403
    assert "session" in json.loads(payload.decode("utf-8"))["error"]

    _command(
        server,
        "create_part_from_ops",
        {"name": "extra", "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]},
    )
    assert "position_error" not in _state(server)["panels"]


def test_the_position_error_form_enters_a_sliding_joint_in_millimetres(server, tmp_path):
    status, script = _exchange(server, "GET", "/static/tools.js", token=None, origin=None)
    assert status == 200
    page = script.decode("utf-8")
    assert "resolution_mm" in page
    assert "n_per_mm" in page
    tools = tmp_path / "tools.js"
    tools.write_text(page, encoding="utf-8")
    node = shutil.which("node")
    assert node
    probe = tmp_path / "probe.js"
    probe.write_text(_FORM_PROBE, encoding="utf-8")
    # Node writes UTF-8 to a pipe. On Windows text mode would use the console
    # code page and turn the middle dot in "N·mm" into a different character.
    completed = subprocess.run(
        [node, str(probe), str(tools)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)

    assert "A prismatic joint uses millimetres and newtons." in report["help"]
    assert "A cylindrical joint has both." in report["help"]
    rail = report["rail"]
    assert 'data-error-unit="mm"' in rail
    assert "Encoder step mm" in rail
    assert "Backlash mm" in rail
    assert "Force N" in rail
    assert "Stiffness N/mm" in rail
    assert "Encoder step deg" not in rail
    assert "Torque" not in rail
    hinge = report["hinge"]
    assert 'data-error-unit="deg"' in hinge
    assert "Encoder step deg" in hinge
    assert "Backlash deg" in hinge
    assert "Torque N·mm" in hinge
    assert "Stiffness N·mm/rad" in hinge
    assert "Encoder step mm" not in hinge
    spin = report["spin"]
    assert 'data-error-unit="cylindrical"' in spin
    assert "data-error-angle" in spin
    assert "data-error-slide" in spin
    assert "Encoder step deg" in spin
    assert "Encoder step mm" in spin

    assert report["prismatic"] == {
        "encoder": {"resolution_mm": 0.02},
        "backlash": {"mm": 0.2},
        "stiffness": {"force_n": 10, "n_per_mm": 100},
    }
    assert report["revolute"] == {
        "encoder": {"resolution_deg": 0.2},
        "stiffness": {"torque_nmm": 12, "nmm_per_rad": 400},
    }
    assert report["cylindrical"] == {
        "angle": {"encoder": {"resolution_deg": 0.2}},
        "slide": {"backlash": {"mm": 0.4}},
    }
    assert report["empty"] is None
    assert report["forceOnly"] == "Enter both the force and the stiffness, or leave both blank."
    assert report["torqueOnly"] == "Enter both the torque and the stiffness, or leave both blank."


def test_a_sliding_joint_check_from_the_form_uses_millimetres(server):
    for name in ("base", "tip"):
        _command(
            server,
            "create_part_from_ops",
            {"name": name, "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]},
        )
    _command(
        server,
        "create_assembly",
        {
            "spec": {
                "name": "slide",
                "instances": [
                    {"name": "base", "part": "base"},
                    {"name": "tip", "part": "tip"},
                ],
                "joints": [
                    {
                        "name": "rail",
                        "type": "prismatic",
                        "parent": "base",
                        "child": "tip",
                        "axis": [1, 0, 0],
                    }
                ],
            },
        },
    )
    report = _command(
        server,
        "check_position_error",
        {
            "name": "slide",
            "tool": "tip",
            "at": [0, 0, 0],
            "tolerance_mm": 0.3,
            "sources": {
                "rail": {
                    "encoder": {"resolution_mm": 0.02},
                    "backlash": {"mm": 0.2},
                    "stiffness": {"force_n": 10, "n_per_mm": 100},
                }
            },
        },
    )
    pose = report["poses"][0]

    def contribution(ident):
        return next(row for row in pose["contributions"] if row["id"] == ident)

    assert contribution("rail.encoder")["vector_mm"] == pytest.approx([0.01, 0.0, 0.0], abs=1e-6)
    assert contribution("rail.backlash")["vector_mm"] == pytest.approx([0.2, 0.0, 0.0], abs=1e-6)
    assert contribution("rail.stiffness")["vector_mm"] == pytest.approx([0.1, 0.0, 0.0], abs=1e-6)
    assert report["worst_case_mm"] == pytest.approx(0.31, abs=1e-6)
    assert report["status"] == "fail"
    assert _state(server)["panels"]["position_error"]["status"] == "fail"

    refused = _refused(
        server,
        "check_position_error",
        {
            "name": "slide",
            "tool": "tip",
            "tolerance_mm": 1.0,
            "sources": {"rail": {"encoder": {"resolution_deg": 0.2}}},
        },
    )
    assert "resolution_mm" in refused
