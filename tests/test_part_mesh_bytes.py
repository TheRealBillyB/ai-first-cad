"""A part's mesh is sent as bytes, not as JSON numbers.

The page turned every number of a part's mesh into a 32-bit array anyway.
Writing a 3 MB mesh as JSON took 43 ms on the server and parsing it took
18 ms on the page, after every move, undo, and redo.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.client import HTTPConnection
from pathlib import Path

import numpy as np
import pytest

from aicad import kernel
from aicad.app import make_server
from aicad.errors import AICADError
from aicad.refs import ref_to_selector
from aicad.workspace import Workspace, read_part_answer

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"
_BOX = [{"op": "add", "shape": "box", "size": [10, 6, 4]}]
_PIN = [{"op": "add", "shape": "cylinder", "radius": 2, "height": 12}]
_ARRAYS = {"positions": "<f4", "normals": "<f4", "triangle_face": "<i4"}
_CLOSE = 1e-3


def _two(execute) -> None:
    execute("create_part_from_ops", {"name": "frame", "ops": _BOX})
    execute("create_part_from_ops", {"name": "arm", "ops": _PIN})


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_MESH_CACHE", str(tmp_path / "meshes"))
    opened = Workspace(tmp_path / "project")
    _two(opened.execute)
    return opened


def _same(got, want) -> bool:
    """Equal, with each decimal number within a thousandth."""
    if isinstance(want, dict):
        return isinstance(got, dict) and set(got) == set(want) and all(_same(got[key], want[key]) for key in want)
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and all(_same(*pair) for pair in zip(got, want))
    if isinstance(want, float) or isinstance(got, float):
        return got == pytest.approx(want, abs=_CLOSE)
    if isinstance(want, str) and isinstance(got, str) and got != want and "/" in want:
        # A reference names a point, and 0.000 and -0.000 are the same point.
        return _same(ref_to_selector(got), ref_to_selector(want))
    return got == want


def _corners(positions, normals) -> np.ndarray:
    """Each corner of each triangle once: where it is and which way its surface faces."""
    both = np.hstack([np.asarray(positions, dtype="<f4").reshape(-1, 3), np.asarray(normals, dtype="<f4").reshape(-1, 3)])
    return np.unique(both, axis=0)


def _is_the_mesh_of(body: bytes, shape, name: str) -> None:
    """The bytes hold a mesh of this solid as it is now: the corners of a fresh mesh, and every other field.

    The corners are compared and not the order of the triangles, because a fresh
    mesh can cut a flat face into other triangles than a mesh the server kept and
    moved. Lengths are compared to a thousandth of a millimeter, because the
    numbers are kept to four decimal places. The moves in these tests are 5 mm
    and more.
    """
    fresh = {**kernel.viewport_mesh(shape), "part": name}
    answer = read_part_answer(body)
    assert answer["ok"] is True
    result = answer["result"]
    assert set(result) == set(fresh)
    for key, kind in _ARRAYS.items():
        assert result[key].dtype == np.dtype(kind)
    assert len(result["positions"]) == len(result["normals"]) == 9 * len(result["triangle_face"])
    assert len(result["triangle_face"]) == len(fresh["triangle_face"])
    sent, made = _corners(result["positions"], result["normals"]), _corners(fresh["positions"], fresh["normals"])
    apart = np.abs(sent[:, None, :] - made[None, :, :]).max(axis=2)
    assert apart.min(axis=1).max() < _CLOSE, "a corner that was sent is not in the fresh mesh"
    assert apart.min(axis=0).max() < _CLOSE, "a corner of the fresh mesh was not sent"
    rest = {key: value for key, value in fresh.items() if key not in _ARRAYS}
    assert _same({key: result[key] for key in rest}, json.loads(json.dumps(rest)))


def _live(workspace, name: str):
    return workspace.document._get(name).shape  # noqa: SLF001


def test_the_bytes_are_a_header_and_then_each_array(workspace):
    _tag, body = workspace.mesh_answer("part", "arm")
    length = int.from_bytes(body[:4], "little")
    # The arrays start on a multiple of 4 bytes, so the page reads them where they lie.
    assert length % 4 == 0
    header = json.loads(body[4 : 4 + length])
    mesh = workspace.mesh("arm")
    assert header["arrays"] == [
        ["positions", "f4", len(mesh["positions"])],
        ["normals", "f4", len(mesh["normals"])],
        ["triangle_face", "i4", len(mesh["triangle_face"])],
    ]
    assert not set(_ARRAYS) & set(header["result"])
    assert len(body) == 4 + length + 4 * sum(count for _name, _kind, count in header["arrays"])
    _is_the_mesh_of(body, _live(workspace, "arm"), "arm")


def test_the_numbers_are_the_ones_the_page_drew_from_json(workspace):
    # The page made 32-bit arrays from the JSON numbers. The bytes are those same 32-bit values.
    mesh = workspace.mesh("arm")
    as_json = json.loads(json.dumps(mesh["positions"]))
    result = read_part_answer(workspace.mesh_answer("part", "arm")[1])["result"]
    assert result["positions"].tobytes() == np.array(as_json, dtype=np.float32).tobytes()
    assert result["triangle_face"].tolist() == mesh["triangle_face"]


def test_a_moved_part_sends_new_bytes_and_no_other_part_does(workspace):
    tags = {name: workspace.mesh_answer("part", name) for name in ("frame", "arm")}
    workspace.execute("place_part", {"name": "arm", "position": [20, 0, 0], "rotation_deg": [0, 0, 30]}, source="ui")
    tag, body = workspace.mesh_answer("part", "arm")
    assert tag != tags["arm"][0] and body != tags["arm"][1]
    _is_the_mesh_of(body, _live(workspace, "arm"), "arm")
    assert workspace.mesh_answer("part", "frame") == tags["frame"]


def test_a_rebuilt_part_sends_the_new_solid(workspace):
    tag, _body = workspace.mesh_answer("part", "arm")
    workspace.execute("update_part_ops", {"name": "arm", "ops": [{"op": "add", "shape": "cylinder", "radius": 3, "height": 12}]}, source="ui")
    new_tag, body = workspace.mesh_answer("part", "arm")
    assert new_tag != tag
    _is_the_mesh_of(body, _live(workspace, "arm"), "arm")
    assert read_part_answer(body)["result"]["bounding_box"]["max"][0] == pytest.approx(3)


def test_undo_and_redo_send_the_solid_that_is_live(workspace):
    first = workspace.mesh_answer("part", "arm")
    workspace.execute("place_part", {"name": "arm", "position": [0, 0, 9], "rotation_deg": [0, 0, 0]}, source="ui")
    moved = workspace.mesh_answer("part", "arm")
    workspace.execute("undo", {}, source="ui")
    undone = workspace.mesh_answer("part", "arm")
    # The same solid in the same place is the same bytes, so the page's held tag still matches.
    assert undone == first
    _is_the_mesh_of(undone[1], _live(workspace, "arm"), "arm")
    workspace.execute("redo", {}, source="ui")
    redone = workspace.mesh_answer("part", "arm")
    assert redone == moved
    _is_the_mesh_of(redone[1], _live(workspace, "arm"), "arm")


def test_a_rebuild_that_fails_keeps_sending_the_live_solid(workspace):
    before = workspace.mesh_answer("part", "arm")
    with pytest.raises(AICADError):
        workspace.execute("update_part_ops", {"name": "arm", "ops": [{"op": "add", "shape": "cylinder", "radius": -1, "height": 12}]}, source="ui")
    after = workspace.mesh_answer("part", "arm")
    assert after == before
    _is_the_mesh_of(after[1], _live(workspace, "arm"), "arm")


def test_bytes_that_are_cut_off_or_too_long_are_refused(workspace):
    _tag, body = workspace.mesh_answer("part", "arm")
    for wrong in (body[:-4], body + b"\0\0\0\0", body[:3], b"", b'{"ok":true,"result":{}}'):
        with pytest.raises(ValueError):
            read_part_answer(wrong)


def _get(server, path, tag=None):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=60)
    headers = {"Origin": f"http://127.0.0.1:{port}", "X-Aicad-Token": server.page_token}
    if tag:
        headers["If-None-Match"] = tag
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, response.getheader("ETag"), response.getheader("Content-Type"), body


@pytest.fixture(params=[False, True], ids=["one process", "geometry worker"])
def server(request, tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setenv("AICAD_MESH_CACHE", str(tmp_path / "meshes"))
    project = tmp_path / "project"
    project.mkdir()
    running = make_server(project, "127.0.0.1", 0, worker=request.param)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        _two(lambda name, arguments: running.workspace.execute(name, arguments, source="ui"))
        yield running
    finally:
        running.shutdown()
        running.server_close()
        if request.param:
            running.workspace.close()


def test_the_server_sends_a_part_as_bytes_and_the_rest_as_json(server):
    status, tag, kind, body = _get(server, "/api/mesh?part=arm")
    assert (status, kind) == (200, "application/octet-stream")
    result = read_part_answer(body)["result"]
    assert result["part"] == "arm" and result["bounding_box"]["max"][2] == pytest.approx(6)
    assert len(result["positions"]) == 9 * len(result["triangle_face"])
    listed_status, _list_tag, listed_kind, listed = _get(server, "/api/mesh?scene=parts")
    assert (listed_status, listed_kind) == (200, "application/json; charset=utf-8")
    # The list names the part's bytes by the tag those bytes are sent under.
    assert {entry["name"]: f'"{entry["tag"]}"' for entry in json.loads(listed)["result"]["parts"]}["arm"] == tag
    assert _get(server, "/api/mesh?part=arm", tag)[::3] == (304, b"")
    # A part that is not there is still a JSON error, which the page reads as before.
    missing_status, _tag, missing_kind, missing = _get(server, "/api/mesh?part=nothing")
    assert missing_status == 400 and missing_kind.startswith("application/json") and json.loads(missing)["ok"] is False


def test_over_the_server_a_move_sends_the_moved_bytes(server):
    _status, tag, _kind, _body = _get(server, "/api/mesh?part=arm")
    server.workspace.execute("place_part", {"name": "arm", "position": [0, 0, 5], "rotation_deg": [0, 0, 0]}, source="ui")
    status, new_tag, _kind, body = _get(server, "/api/mesh?part=arm", tag)
    assert status == 200 and new_tag != tag
    box = read_part_answer(body)["result"]["bounding_box"]
    assert (box["min"][2], box["max"][2]) == (pytest.approx(-1), pytest.approx(11))
    positions = read_part_answer(body)["result"]["positions"].reshape(-1, 3)
    assert (float(positions[:, 2].min()), float(positions[:, 2].max())) == (pytest.approx(-1), pytest.approx(11))


_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const elements = {};
function make(id) {
  const node = {
    id, value: "", textContent: "", hidden: false, innerHTML: "", className: "", style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    setAttribute() {}, removeAttribute() {}, getAttribute() { return ""; }, appendChild() {}, focus() {}, click() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 300 }; },
  };
  elements[id] = node;
  return node;
}
const sandbox = {
  console, setTimeout, TextDecoder, DataView, Uint8Array, Float32Array, Int32Array, ArrayBuffer,
  document: {
    getElementById(id) { return elements[id] || make(id); },
    querySelector() { return { getAttribute() { return ""; } }; },
    querySelectorAll() { return []; },
    addEventListener() {},
    createElement() { return make("el"); },
  },
  sessionStorage: { getItem() { return null; }, setItem() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { userAgent: "test" },
  location: { origin: "http://127.0.0.1:8765", href: "http://127.0.0.1:8765/" },
  fetch: () => Promise.reject(new Error("no fetch in this probe")),
  WebSocket: function () {},
  requestAnimationFrame() {}, cancelAnimationFrame() {},
};
sandbox.window = sandbox;
// What the server sent for each part, as bytes on disk, and the scene list as JSON.
const sent = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
sandbox.bytesOf = (file) => { const data = fs.readFileSync(file); return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength); };
sandbox.sent = sent;
vm.createContext(sandbox);
const driver = `
const answers = { frame: sent.frame, arm: sent.arm };
const asked = [];
const read = [];
apiFetch = async (url, options) => {
  const held = (options && options.headers && options.headers["If-None-Match"]) || null;
  const name = url.includes("part=") ? url.split("part=")[1] : null;
  const answer = name ? answers[name] : { tag: sent.listTag, list: sent.list };
  asked.push([url, held]);
  return {
    status: held === answer.tag ? 304 : 200,
    headers: { get: (key) => (key === "ETag" ? answer.tag : key === "Content-Type" ? (name ? "application/octet-stream" : "application/json; charset=utf-8") : null) },
    json: async () => { read.push(["json", url]); if (name) throw new Error("a part is not JSON"); return JSON.parse(answer.list); },
    arrayBuffer: async () => { read.push(["bytes", url]); return bytesOf(answer.file); },
  };
};
ui.page = "model";
ui.viewAssembly = null;
ui.state = { parts: [{ name: "frame" }, { name: "arm" }], sketches: [], panels: {}, assemblies: [] };
async function probe() {
  const out = {};
  await loadMesh();
  out.read = read.splice(0);
  const arm = meshAnswers.get("/api/mesh?part=arm").result;
  out.kinds = [arm.positions instanceof Float32Array, arm.normals instanceof Float32Array, arm.triangle_face instanceof Int32Array];
  out.arm = { positions: Array.from(arm.positions), normals: Array.from(arm.normals), triangleFace: Array.from(arm.triangle_face), faces: arm.faces.length, edges: arm.edges.length, box: arm.bounding_box, part: arm.part };
  out.scene = { parts: ui.mesh.parts, vertices: ui.mesh.positions.length / 3, box: ui.mesh.bounding_box };
  const frameCount = meshAnswers.get("/api/mesh?part=frame").result.positions.length;
  out.armInScene = Array.from(ui.mesh.positions.slice(frameCount));
  // The arm is moved, and its new bytes arrive cut off. Then they arrive with bytes left over.
  const shown = ui.mesh;
  out.firstTag = sent.arm.tag;
  for (const [label, file] of [["cut", sent.cut], ["long", sent.long]]) {
    answers.arm = { tag: sent.moved.tag, file };
    sent.listTag = sent.movedListTag;
    sent.list = sent.movedList;
    let failed = null;
    try { await loadMesh(); } catch (error) { failed = String(error && error.message || error); }
    out[label] = { failed, sameScene: ui.mesh === shown, held: meshAnswers.get("/api/mesh?part=arm").tag };
  }
  // The whole bytes arrive, and the page shows the moved arm.
  answers.arm = sent.moved;
  await loadMesh();
  out.movedRead = read.splice(0).filter((row) => row[0] === "bytes").map((row) => row[1]);
  out.movedInScene = Array.from(ui.mesh.positions.slice(frameCount));
  return out;
}
probe().then((value) => { report = value; done(); }, (error) => { report = { error: String(error && error.stack || error) }; done(); });
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
"""


@pytest.fixture
def page(workspace, tmp_path):
    """The page in Node, reading the bytes this server made."""
    node = shutil.which("node")
    assert node

    def kept(name: str, label: str) -> dict:
        tag, body = workspace.mesh_answer("part", name)
        path = tmp_path / f"{label}.bin"
        path.write_bytes(body)
        return {"tag": f'"{tag}"', "file": str(path)}

    list_tag, listed = workspace.mesh_answer("scene_parts")
    sent = {"frame": kept("frame", "frame"), "arm": kept("arm", "arm"), "listTag": f'"{list_tag}"', "list": listed.decode("utf-8")}
    first = read_part_answer(workspace.mesh_answer("part", "arm")[1])["result"]
    workspace.execute("place_part", {"name": "arm", "position": [0, 0, 5], "rotation_deg": [0, 0, 0]}, source="ui")
    sent["moved"] = kept("arm", "moved")
    moved_list_tag, moved_list = workspace.mesh_answer("scene_parts")
    sent["movedListTag"], sent["movedList"] = f'"{moved_list_tag}"', moved_list.decode("utf-8")
    moved_body = workspace.mesh_answer("part", "arm")[1]
    (tmp_path / "cut.bin").write_bytes(moved_body[:-400])
    (tmp_path / "long.bin").write_bytes(moved_body + b"\0" * 8)
    sent["cut"], sent["long"] = str(tmp_path / "cut.bin"), str(tmp_path / "long.bin")
    (tmp_path / "sent.json").write_text(json.dumps(sent), encoding="utf-8")
    probe = tmp_path / "bytes-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(STATIC / "app.js"), str(tmp_path / "sent.json")], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    report = json.loads(result.stdout)
    assert "error" not in report, report
    report["first"] = first
    report["movedMesh"] = read_part_answer(moved_body)["result"]
    return report


def test_the_page_reads_the_arrays_from_the_bytes(page):
    # Each part was read as bytes and never as JSON. Only the list is JSON.
    assert page["read"] == [["json", "/api/mesh?scene=parts"], ["bytes", "/api/mesh?part=frame"], ["bytes", "/api/mesh?part=arm"]]
    assert page["kinds"] == [True, True, True]
    first = page["first"]
    assert np.array_equal(np.array(page["arm"]["positions"], dtype=np.float32), first["positions"])
    assert np.array_equal(np.array(page["arm"]["normals"], dtype=np.float32), first["normals"])
    assert page["arm"]["triangleFace"] == first["triangle_face"].tolist()
    assert (page["arm"]["faces"], page["arm"]["edges"]) == (len(first["faces"]), len(first["edges"]))
    assert page["arm"]["box"] == first["bounding_box"] and page["arm"]["part"] == "arm"
    # The scene the page draws holds the arm's corners as they were sent.
    assert page["scene"]["parts"] == ["frame", "arm"]
    assert np.array_equal(np.array(page["armInScene"], dtype=np.float32), first["positions"])


def test_the_page_never_shows_part_of_a_cut_off_mesh(page):
    for label in ("cut", "long"):
        # The read failed, the scene on screen is the one from before, and the old mesh is still held under its old tag.
        assert page[label]["failed"], label
        assert page[label]["sameScene"] is True
        assert page[label]["held"] == page["firstTag"]


def test_the_page_shows_the_moved_part_from_its_new_bytes(page):
    assert page["movedRead"][-1] == "/api/mesh?part=arm"
    assert np.array_equal(np.array(page["movedInScene"], dtype=np.float32), page["movedMesh"]["positions"])
    assert float(np.array(page["movedInScene"]).reshape(-1, 3)[:, 2].min()) == pytest.approx(-1)
