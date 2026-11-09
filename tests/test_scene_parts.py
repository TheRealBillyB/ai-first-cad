"""The model view is sent part by part, so a move sends the part that moved.

The page used to read the model view as one mesh of every part. Moving one
part on a 38-part finger rig sent all 48 MB of it again.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aicad import kernel
from aicad.app import make_server
from aicad.studio import part_color
from aicad.workspace import Workspace

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"
_BOX = [{"op": "add", "shape": "box", "size": [10, 6, 4]}]


def _three(execute) -> None:
    for name in ("frame", "arm", "lid"):
        execute("create_part_from_ops", {"name": name, "ops": _BOX})


def test_the_scene_list_names_each_part_mesh_by_its_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    workspace = Workspace(tmp_path / "project")
    _three(workspace.execute)
    _tag, body = workspace.mesh_answer("scene_parts")
    listed = json.loads(body)["result"]
    assert [entry["name"] for entry in listed["parts"]] == ["frame", "arm", "lid"]
    assert [entry["color"] for entry in listed["parts"]] == [part_color(0), part_color(1), part_color(2)]
    for entry in listed["parts"]:
        assert entry["tag"] == workspace.mesh_answer("part", entry["name"])[0]

    meshed = []
    real = kernel.viewport_mesh
    monkeypatch.setattr(kernel, "viewport_mesh", lambda shape: meshed.append(id(shape)) or real(shape))
    workspace.execute("place_part", {"name": "arm", "position": [20, 0, 0], "rotation_deg": [0, 0, 0]}, source="ui")
    _tag, moved_body = workspace.mesh_answer("scene_parts")
    moved = {entry["name"]: entry["tag"] for entry in json.loads(moved_body)["result"]["parts"]}
    before = {entry["name"]: entry["tag"] for entry in listed["parts"]}
    # Only the moved part has a new tag, and only it was meshed.
    assert [name for name in moved if moved[name] != before[name]] == ["arm"]
    assert meshed == [id(workspace.document._get("arm").shape)]  # noqa: SLF001


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
    return response.status, response.getheader("ETag"), body


@pytest.fixture(params=[False, True], ids=["one process", "geometry worker"])
def server(request, tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    project = tmp_path / "project"
    project.mkdir()
    running = make_server(project, "127.0.0.1", 0, worker=request.param)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    try:
        _three(lambda name, arguments: running.workspace.execute(name, arguments, source="ui"))
        yield running
    finally:
        running.shutdown()
        running.server_close()
        if request.param:
            running.workspace.close()


def test_after_a_move_only_the_moved_part_is_sent(server):
    status, list_tag, body = _get(server, "/api/mesh?scene=parts")
    assert status == 200
    held = {}
    for entry in json.loads(body)["result"]["parts"]:
        part_status, tag, _mesh = _get(server, f"/api/mesh?part={entry['name']}")
        assert part_status == 200 and tag == f'"{entry["tag"]}"'
        held[entry["name"]] = tag
    assert _get(server, "/api/mesh?scene=parts", list_tag)[0] == 304

    server.workspace.execute("place_part", {"name": "lid", "position": [0, 0, 12], "rotation_deg": [0, 0, 0]}, source="ui")
    status, new_list_tag, body = _get(server, "/api/mesh?scene=parts", list_tag)
    assert status == 200 and new_list_tag != list_tag
    listed = {entry["name"]: f'"{entry["tag"]}"' for entry in json.loads(body)["result"]["parts"]}
    assert [name for name in listed if listed[name] != held[name]] == ["lid"]
    # The page asks only for lid. The others, asked anyway, are 304 with no body.
    assert _get(server, "/api/mesh?part=frame", held["frame"])[:3:2] == (304, b"")
    lid_status, _tag, lid = _get(server, "/api/mesh?part=lid", held["lid"])
    assert lid_status == 200 and json.loads(lid)["result"]["bounding_box"]["min"][2] == pytest.approx(10)


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
  console, setTimeout,
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
vm.createContext(sandbox);
const driver = `
const triangle = (dx, part) => ({
  part,
  positions: [dx, 0, 0, dx + 1, 0, 0, dx, 1, 0],
  normals: [0, 0, 1, 0, 0, 1, 0, 0, 1],
  triangle_face: [0],
  faces: [{ index: 0, kind: "plane", ref: "face/plane/" + part }],
  edges: [{ index: 0, kind: "line", ref: "edge/line/" + part, points: [[dx, 0, 0], [dx + 1, 0, 0]] }],
});
const meshes = { a: { tag: "a1", body: triangle(0, "a") }, b: { tag: "b1", body: triangle(10, "b") } };
const listing = () => ({
  tag: "list-" + meshes.a.tag + meshes.b.tag,
  body: { scene: true, unbuilt: ["c"], parts: [
    { name: "a", tag: meshes.a.tag, color: [200, 0, 0] },
    { name: "b", tag: meshes.b.tag, color: [0, 0, 200] },
  ] },
});
const asked = [];
const parsed = [];
// Runs once, as the next part request reaches the server: a change made between the two reads.
let beforePart = null;
apiFetch = async (url, options) => {
  const held = (options && options.headers && options.headers["If-None-Match"]) || null;
  const name = url.includes("part=") ? url.split("part=")[1] : null;
  if (name && beforePart) { const change = beforePart; beforePart = null; change(); }
  const answer = name ? meshes[name] : listing();
  const tag = '"' + answer.tag + '"';
  asked.push([url, held]);
  return {
    status: held === tag ? 304 : 200,
    headers: { get: (key) => (key === "ETag" ? tag : null) },
    json: async () => { parsed.push(url); return { ok: true, result: JSON.parse(JSON.stringify(answer.body)) }; },
  };
};
ui.page = "model";
ui.viewAssembly = null;
ui.state = { parts: [{ name: "a" }, { name: "b" }], sketches: [], panels: {}, assemblies: [] };
const look = () => ({
  parts: ui.mesh.parts,
  positions: Array.from(ui.mesh.positions),
  colors: Array.from(ui.mesh.colors),
  triangleFace: Array.from(ui.mesh.triangle_face),
  trianglePart: ui.mesh.triangle_part,
  faces: ui.mesh.faces.map((face) => [face.ref, face.part]),
  edges: ui.mesh.edges.map((edge) => [edge.ref, edge.part]),
  box: ui.mesh.bounding_box,
  unbuilt: ui.mesh.unbuilt,
});
async function probe() {
  const out = {};
  await loadMesh();
  out.first = look();
  out.firstAsked = asked.splice(0);
  parsed.splice(0);
  const firstMesh = ui.mesh;
  await loadMesh();
  out.idleAsked = asked.splice(0);
  out.idleParsed = parsed.splice(0);
  out.idleSameMesh = ui.mesh === firstMesh;
  meshes.b = { tag: "b2", body: triangle(20, "b") };
  await loadMesh();
  out.movedAsked = asked.splice(0);
  out.movedParsed = parsed.splice(0);
  out.moved = look();
  // b moves to 30. The page reads the list, and before it asks for b, b moves again, to 40.
  meshes.b = { tag: "b3", body: triangle(30, "b") };
  beforePart = () => { meshes.b = { tag: "b4", body: triangle(40, "b") }; };
  await loadMesh();
  out.racedAsked = asked.splice(0);
  out.raced = look();
  // Undo puts b back at 30 before the next refresh. The list is the one the page first read.
  meshes.b = { tag: "b3", body: triangle(30, "b") };
  await loadMesh();
  out.undoneAsked = asked.splice(0);
  out.undone = look();
  return out;
}
probe().then((value) => { report = value; done(); }, (error) => { report = { error: String(error && error.stack || error) }; done(); });
`;
sandbox.done = () => process.stdout.write(JSON.stringify(sandbox.report));
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\n" + driver, sandbox, { filename: "app.js" });
"""


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    node = shutil.which("node")
    assert node
    probe = tmp_path_factory.mktemp("page") / "scene-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(STATIC / "app.js")], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    report = json.loads(result.stdout)
    assert "error" not in report, report
    return report


def test_the_page_joins_the_parts_into_one_scene(page):
    first = page["first"]
    assert first["parts"] == ["a", "b"]
    assert first["positions"] == [0, 0, 0, 1, 0, 0, 0, 1, 0, 10, 0, 0, 11, 0, 0, 10, 1, 0]
    assert first["colors"] == [200, 0, 0] * 3 + [0, 0, 200] * 3
    # Each part's face numbers move past the faces before it, and keep their part.
    assert first["triangleFace"] == [0, 1]
    assert first["trianglePart"] == ["a", "b"]
    assert first["faces"] == [["face/plane/a", "a"], ["face/plane/b", "b"]]
    assert first["edges"] == [["edge/line/a", "a"], ["edge/line/b", "b"]]
    assert first["box"] == {"min": [0, 0, 0], "max": [11, 1, 0]}
    assert first["unbuilt"] == ["c"]


def test_the_page_asks_for_each_part_once(page):
    assert page["firstAsked"] == [
        ["/api/mesh?scene=parts", None],
        ["/api/mesh?part=a", None],
        ["/api/mesh?part=b", None],
    ]
    # Nothing changed: the list is a 304, and no part is asked for.
    assert page["idleAsked"] == [["/api/mesh?scene=parts", '"list-a1b1"']]
    assert page["idleParsed"] == []
    assert page["idleSameMesh"] is True


def test_after_a_move_the_page_reads_only_the_moved_part(page):
    assert page["movedAsked"] == [["/api/mesh?scene=parts", '"list-a1b1"'], ["/api/mesh?part=b", '"b1"']]
    assert page["movedParsed"] == ["/api/mesh?scene=parts", "/api/mesh?part=b"]
    assert page["moved"]["positions"][9:12] == [20, 0, 0]


def test_a_part_newer_than_the_list_is_not_kept_under_it(page):
    # b answered with a tag the list did not name, so the page read the list again.
    assert page["racedAsked"] == [
        ["/api/mesh?scene=parts", '"list-a1b2"'],
        ["/api/mesh?part=b", '"b2"'],
        ["/api/mesh?scene=parts", '"list-a1b3"'],
    ]
    assert page["raced"]["positions"][9:12] == [40, 0, 0]


def test_undo_after_a_move_between_the_reads_shows_the_undone_part(page):
    assert page["undone"]["positions"][9:12] == [30, 0, 0]
    # The scene at 40 was kept under the list that names b at 40, not the list that names b at 30.
    assert page["undoneAsked"] == [["/api/mesh?scene=parts", '"list-a1b4"'], ["/api/mesh?part=b", '"b4"']]
