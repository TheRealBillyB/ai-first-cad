"""A command's answer carries the state and the scene list the page reads next.

After an undo, a redo, a move, or a joint change, the page asked for the state
and then the scene list, each in a request of its own, right after the command.
The page now asks the command to carry them, and the server reads them once
the command has run, so a refresh after a command makes no request for them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aicad.app import make_server

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"

_BOX = [{"op": "add", "shape": "box", "size": [10, 6, 4]}]
_PIN = [{"op": "add", "shape": "cylinder", "radius": 2, "height": 12}]


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setenv("AICAD_MESH_CACHE", str(tmp_path / "meshes"))
    return tmp_path


def _serve(project, worker=False):
    server = make_server(project, "127.0.0.1", 0, worker=worker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop(server):
    server.shutdown()
    server.server_close()
    close = getattr(server.workspace, "close", None)
    if close is not None:
        close()


def _exchange(server, method, path, body=None, headers=None):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=120)
    sent = {"Origin": f"http://127.0.0.1:{port}", "X-Aicad-Token": server.page_token}
    sent.update(headers or {})
    data = None
    if body is not None:
        sent["Content-Type"] = "application/json"
        data = json.dumps(body)
    connection.request(method, path, body=data, headers=sent)
    response = connection.getresponse()
    answer = response.read()
    connection.close()
    return response.status, response.getheader("ETag"), answer


def _command(server, name, arguments=None, then=None):
    body = {"name": name, "arguments": arguments or {}}
    if then is not None:
        body["then"] = then
    status, _tag, answer = _exchange(server, "POST", "/api/command", body)
    return status, json.loads(answer)


def _state(server):
    status, _tag, answer = _exchange(server, "GET", "/api/state")
    assert status == 200
    return json.loads(answer)["result"]


def _scene_list(server):
    status, tag, answer = _exchange(server, "GET", "/api/mesh?scene=parts")
    assert status == 200
    return tag, json.loads(answer)["result"]


def _rig(server):
    for name, ops in (("frame", _BOX), ("arm", _PIN)):
        status, answer = _command(server, "create_part_from_ops", {"name": name, "ops": ops})
        assert status == 200, answer


def _move(x):
    return {"name": "arm", "position": [x, 0, 0], "rotation_deg": [0, 0, 0]}


@pytest.mark.parametrize("worker", [False, True], ids=["one process", "geometry worker"])
def test_the_answer_carries_what_the_page_would_read_next(environment, worker):
    server = _serve(environment / "project", worker=worker)
    try:
        _rig(server)
        _tag, before = _scene_list(server)
        for name, arguments in (("place_part", _move(30)), ("undo", {}), ("redo", {})):
            status, answer = _command(server, name, arguments, then=["state", "scene"])
            assert status == 200, answer
            # The same state and the same list, under the same tag, as the two requests it replaces.
            assert answer["state"] == _state(server)
            tag, listed = _scene_list(server)
            assert answer["scene"] == {"tag": tag, "result": listed}
        # The list names the moved part's new mesh, and the part answers under that tag.
        arm = {entry["name"]: entry["tag"] for entry in answer["scene"]["result"]["parts"]}["arm"]
        assert arm != {entry["name"]: entry["tag"] for entry in before["parts"]}["arm"]
        _status, part_tag, _body = _exchange(server, "GET", "/api/mesh?part=arm")
        assert part_tag == f'"{arm}"'
    finally:
        _stop(server)


def test_a_command_that_asks_for_nothing_answers_as_before(environment):
    server = _serve(environment / "project")
    try:
        _rig(server)
        status, answer = _command(server, "place_part", _move(10))
        assert status == 200
        assert set(answer) == {"ok", "revision", "result"}
        status, answer = _command(server, "undo", {}, then=["state"])
        assert status == 200
        assert "state" in answer and "scene" not in answer
    finally:
        _stop(server)


@pytest.mark.parametrize("then", ["state", ["state", "parts"], [1], {"state": True}])
def test_an_unknown_read_is_refused_before_the_command_runs(environment, then):
    server = _serve(environment / "project")
    try:
        _rig(server)
        status, answer = _command(server, "place_part", _move(40), then=then)
        assert status == 400
        assert '"then"' in answer["error"]
        # Nothing moved.
        placed = {part["name"]: part for part in _state(server)["parts"]}["arm"]
        assert placed["placement"]["position"] == [0, 0, 0]
    finally:
        _stop(server)


def test_a_failed_command_carries_nothing(environment):
    server = _serve(environment / "project")
    try:
        _rig(server)
        status, answer = _command(server, "place_part", {"name": "missing", "position": [1, 0, 0]}, then=["state", "scene"])
        assert status == 400
        assert set(answer) == {"ok", "error"}
    finally:
        _stop(server)


def test_a_read_that_fails_after_the_command_is_left_out(environment, monkeypatch):
    server = _serve(environment / "project")
    try:
        _rig(server)

        def broken(*_args, **_kwargs):
            raise RuntimeError("the list could not be read")

        monkeypatch.setattr(server.workspace, "mesh_answer", broken)
        status, answer = _command(server, "place_part", _move(25), then=["state", "scene"])
        # The command ran and says so. The page reads the list itself.
        assert status == 200
        assert "scene" not in answer
        assert {part["name"]: part for part in answer["state"]["parts"]}["arm"]["placement"]["position"] == [25, 0, 0]
    finally:
        _stop(server)


# The page, run in Node with the server's answers stood in.

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const elements = {};
function make(id) {
  const node = {
    id, value: "", textContent: "", hidden: false, disabled: false, innerHTML: "", className: "", title: "", style: {}, dataset: {},
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
const mesh = (dx) => ({ positions: new Float32Array([dx, 0, 0, dx + 1, 0, 0, dx, 1, 0]), normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
  triangle_face: new Int32Array([0]), faces: [{ index: 0, ref: "face/" + dx }], edges: [], bounding_box: { min: [dx, 0, 0], max: [dx + 1, 1, 0] } });
const list = (armTag) => ({ scene: true, parts: [{ name: "frame", tag: "f1", color: [1, 0, 0] }, { name: "arm", tag: armTag, color: [0, 0, 1] }], unbuilt: [] });
const server = { event: 7, state: null, list: list("a1"), parts: { frame: ["f1", mesh(0)], arm: ["a1", mesh(10)] } };
const stateOf = () => ({ event: server.event, parts: [{ name: "frame" }, { name: "arm" }], sketches: [], panels: {}, assemblies: [], plans: [] });
const asked = [];
apiFetch = async (url, options = {}) => {
  const headers = options.headers || {};
  const held = (headers instanceof Map ? null : headers["If-None-Match"]) || null;
  asked.push(options.method === "POST" ? ["POST", url, JSON.parse(options.body)] : ["GET", url]);
  if (url === "/api/command") {
    const sent = JSON.parse(options.body);
    const body = { ok: true, revision: 1, result: {} };
    if (sent.then && sent.then.includes("state")) body.state = stateOf();
    if (sent.then && sent.then.includes("scene")) body.scene = { tag: '"' + server.listTag + '"', result: server.list };
    return { status: 200, headers: { get: () => null }, json: async () => body };
  }
  if (url === "/api/state") return { status: 200, headers: { get: () => null }, json: async () => ({ ok: true, result: stateOf() }) };
  if (url === "/api/mesh?scene=parts") {
    const tag = '"' + server.listTag + '"';
    return { status: held === tag ? 304 : 200, headers: { get: (name) => (name === "ETag" ? tag : null) }, json: async () => ({ ok: true, result: server.list }) };
  }
  const name = decodeURIComponent(url.split("part=")[1]);
  const [tag, body] = server.parts[name];
  return { status: 200, headers: { get: (key) => (key === "ETag" ? '"' + tag + '"' : key === "Content-Type" ? "application/json" : null) },
    json: async () => ({ ok: true, result: body }), arrayBuffer: async () => { throw new Error("json in this probe"); } };
};
server.listTag = "L1";
// What a refresh draws and lists is not what is checked here.
for (const name of ["renderTree", "renderPanel", "forgetDocumentOfOtherProject", "renderDocs", "renderAssemblies", "renderDimensions",
  "renderProjectWarning", "renderScriptWarning", "renderActivity", "renderShape", "drawSolid", "showEmpty", "renderBusyNote", "sketchPendingPoint"]) {
  globalThis[name] = () => {};
}
const meshLoads = [];
const realLoadMesh = loadMesh;
async function probe() {
  const out = {};
  ui.page = "model"; ui.stage = "solid"; ui.viewAssembly = null;
  ui.state = stateOf();

  // What each kind of call asks the command to carry.
  asked.length = 0;
  await command("list_parameters", { name: "arm" });
  await changeCommand("undo");
  ui.viewAssembly = "rig";
  await changeCommand("set_joint", { name: "rig", joint: "hinge", value: 5 });
  ui.viewAssembly = null;
  out.thenAsked = asked.map((row) => row[2].then || null);

  // A refresh after a command takes what it carried and asks for neither.
  loadMesh = async (options) => { meshLoads.push(options.sceneList ? options.sceneList.tag : null); };
  server.event = 8;
  asked.length = 0;
  let reads = await changeCommand("undo");
  await refresh({ fit: "scene", reads });
  out.carriedAsked = asked.map((row) => row[0] + " " + row[1]);
  out.carriedEvent = ui.state.event;
  out.carriedList = meshLoads.splice(0);

  // An assistant's change was shown in between: the carried state is older, so the refresh asks.
  server.event = 9;
  reads = await changeCommand("redo");
  reads.state.event = 9;
  server.event = 10;
  await refresh();
  asked.length = 0;
  meshLoads.length = 0;
  await refresh({ reads });
  out.olderAsked = asked.map((row) => row[0] + " " + row[1]);
  out.olderEvent = ui.state.event;
  out.olderList = meshLoads.splice(0);

  // A refresh with nothing carried asks, as before.
  asked.length = 0;
  await refresh();
  out.plainAsked = asked.map((row) => row[0] + " " + row[1]);
  meshLoads.length = 0;
  loadMesh = realLoadMesh;

  // The scene: a carried list is not asked for, and only the part whose tag changed is fetched.
  asked.length = 0;
  let read = await sceneParts(null);
  ui.mesh = sceneMesh(read.index.result, read.parts.map((part) => part.result));
  ui.mesh._index = read.index.result;
  server.parts.arm = ["a2", mesh(20)];
  server.list = list("a2");
  server.listTag = "L2";
  asked.length = 0;
  read = await sceneParts({ tag: '"L2"', result: server.list });
  out.movedAsked = asked.map((row) => row[1]);
  out.movedMatched = read.matched === true;
  out.movedFirst = read.parts.map((part) => part.result.positions[0]);
  ui.mesh = sceneMesh(read.index.result, read.parts.map((part) => part.result));
  ui.mesh._index = read.index.result;

  // The same list again, under the tag held: the scene on screen is kept, with no request.
  asked.length = 0;
  read = await sceneParts({ tag: '"L2"', result: list("a2") });
  out.sameAsked = asked.length;
  out.sameHeld = read.held === true;

  // The model changed again after the list was carried: the part answers a tag the list
  // does not name, so the list is asked for and the newest part is shown.
  server.parts.arm = ["a3", mesh(30)];
  server.list = list("a3");
  server.listTag = "L3";
  asked.length = 0;
  read = await sceneParts({ tag: '"L2x"', result: list("a2x") });
  out.racedAsked = asked.map((row) => row[1]);
  out.racedMatched = read.matched === true;
  out.racedFirst = read.parts.map((part) => part.result.positions[0]);

  // Loads that join: a later ask with no list reads the list, so a carried one is never kept past it.
  const lists = [];
  const realSceneParts = sceneParts;
  sceneParts = async (given) => { lists.push(given ? given.tag : null); return { ok: true, held: true }; };
  ui.mesh = { positions: [0, 0, 0], parts: ["frame", "arm"] };
  const joined = [loadMesh({ sceneList: { tag: '"old"', result: list("a1") } }), loadMesh({})];
  await Promise.all(joined).catch(() => {});
  out.joinedLists = lists.splice(0);
  await loadMesh({ sceneList: { tag: '"new"', result: list("a3") } }).catch(() => {});
  out.ownList = lists.splice(0);
  sceneParts = realSceneParts;
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
    probe = tmp_path_factory.mktemp("page") / "next-reads-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(STATIC / "app.js")], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    report = json.loads(result.stdout)
    assert "error" not in report, report
    return report


def test_the_page_asks_a_command_to_carry_only_what_it_reads_next(page):
    # A command whose answer is read as it is carries nothing. On Model the page reads the
    # state and the scene list. On an assembly it reads the state and the pose.
    assert page["thenAsked"] == [None, ["state", "scene"], ["state"]]


def test_a_refresh_after_a_command_asks_for_neither(page):
    assert page["carriedAsked"] == ["POST /api/command"]
    assert page["carriedEvent"] == 8
    assert page["carriedList"] == ['"L1"']


def test_a_carried_state_older_than_the_one_shown_is_not_used(page):
    assert page["olderAsked"] == ["GET /api/state"]
    assert page["olderEvent"] == 10
    assert page["olderList"] == [None]


def test_a_refresh_with_nothing_carried_asks_as_before(page):
    assert page["plainAsked"] == ["GET /api/state"]


def test_a_carried_list_fetches_only_the_moved_part(page):
    assert page["movedAsked"] == ["/api/mesh?part=arm"]
    assert page["movedMatched"] is True
    assert page["movedFirst"] == [0, 20]


def test_a_carried_list_under_the_held_tag_keeps_the_scene(page):
    assert page["sameAsked"] == 0
    assert page["sameHeld"] is True


def test_a_list_overtaken_by_a_newer_change_is_read_again(page):
    # The part answered a tag the carried list did not name, so the list was asked for.
    assert page["racedAsked"][0] == "/api/mesh?part=arm"
    assert "/api/mesh?scene=parts" in page["racedAsked"]
    assert page["racedMatched"] is True
    assert page["racedFirst"] == [0, 30]


def test_a_load_joined_by_a_later_ask_reads_the_list(page):
    assert page["joinedLists"] == [None]
    assert page["ownList"] == ['"new"']
