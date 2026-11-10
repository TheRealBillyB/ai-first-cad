"""A mesh is made once per solid and sent once per change.

A slider release in the assembly view used to rebuild every instance's mesh
and send all of it again, and every refresh re-encoded and resent the scene,
including the busy check every 2 seconds. Every change also cleared every
part's mesh, so moving one part meshed all of them again.
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
from aicad.app_worker import GeometryWorker
from aicad.assembly import placed_instances, stored_piece
from aicad.workspace import Workspace

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"

_BOX = [{"op": "add", "shape": "box", "size": [10, 6, 4]}]
_PIN = [{"op": "add", "shape": "cylinder", "radius": 2, "height": 12}]


def _rig(execute) -> None:
    execute("create_part_from_ops", {"name": "frame", "ops": _BOX})
    execute("create_part_from_ops", {"name": "arm", "ops": _PIN})
    execute(
        "create_assembly",
        {
            "spec": {
                "name": "rig",
                "instances": [
                    {"name": "frame", "part": "frame"},
                    {"name": "arm", "part": "arm"},
                    {"name": "arm2", "part": "arm"},
                ],
                "joints": [
                    {"name": "hinge", "type": "revolute", "parent": "frame", "child": "arm", "axis": [0, 0, 1], "parent_anchor": [5, 0, 0]},
                    {"name": "slide", "type": "prismatic", "parent": "frame", "child": "arm2", "axis": [1, 0, 0], "min": 0, "max": 20},
                ],
            }
        },
    )


class _Counted:
    """Counts the calls that mesh a solid."""

    def __init__(self, monkeypatch):
        self.viewport: list[int] = []
        self.tessellate: list[int] = []
        real_viewport = kernel.viewport_mesh
        real_tessellate = kernel.tessellate

        def viewport(shape):
            self.viewport.append(id(shape))
            return real_viewport(shape)

        def tessellate(shape):
            self.tessellate.append(id(shape))
            return real_tessellate(shape)

        monkeypatch.setattr(kernel, "viewport_mesh", viewport)
        monkeypatch.setattr(kernel, "tessellate", tessellate)

    def reset(self) -> None:
        self.viewport.clear()
        self.tessellate.clear()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "rig")
    _rig(workspace.execute)
    counted = _Counted(monkeypatch)
    # The first look meshes everything. That is not what is measured.
    tags = {kind: workspace.mesh_answer(kind, name)[0] for kind, name in (("scene", ""), ("pieces", "rig"), ("pose", "rig"))}
    counted.reset()
    return workspace, counted, tags


def test_a_joint_change_meshes_nothing_and_changes_only_the_pose(rig):
    workspace, counted, tags = rig
    workspace.execute("set_joint", {"name": "rig", "joint": "hinge", "value": 40}, source="ui")
    scene_tag, scene = workspace.mesh_answer("scene")
    pieces_tag, _pieces = workspace.mesh_answer("pieces", "rig")
    pose_tag, pose = workspace.mesh_answer("pose", "rig")
    assert counted.viewport == []
    assert counted.tessellate == []
    assert scene_tag == tags["scene"]
    assert pieces_tag == tags["pieces"]
    assert pose_tag != tags["pose"]
    # The pose is a few hundred bytes. The triangles stay where the page read them.
    assert len(pose) < 2000
    instances = {row["name"]: row for row in json.loads(pose)["result"]["instances"]}
    assert instances["arm"]["part"] == "arm"
    assert instances["arm"]["matrix"][0][0] == pytest.approx(np.cos(np.radians(40)), abs=1e-6)


def test_a_move_meshes_nothing_and_names_each_face_where_it_now_is(rig):
    workspace, counted, tags = rig
    before = workspace.mesh("arm")
    counted.reset()
    workspace.execute("place_part", {"name": "arm", "position": [30, 0, 0], "rotation_deg": [0, 0, 0]}, source="ui")
    scene_tag, scene = workspace.mesh_answer("scene")
    pieces_tag, _pieces = workspace.mesh_answer("pieces", "rig")
    arm = workspace.document._get("arm")  # noqa: SLF001
    # A move changes no face, so the mesh is carried there and nothing is meshed.
    assert counted.viewport == []
    assert counted.tessellate == []
    assert scene_tag != tags["scene"]
    # A face reference names where the face is, so each one is the moved solid's own.
    moved = workspace.mesh("arm")
    assert [face["ref"] for face in moved["faces"]] == [face["ref"] for face in kernel.describe_faces(arm.shape)]
    assert [face["ref"] for face in moved["faces"]] != [face["ref"] for face in before["faces"]]
    # The assembly places each part's own solid, which a move in Model leaves alone.
    assert pieces_tag == tags["pieces"]
    box = json.loads(scene)["result"]["bounding_box"]
    assert box["max"][0] >= 30


def test_a_geometry_change_meshes_that_part_again_and_no_other(rig):
    workspace, counted, tags = rig
    workspace.execute("update_part_ops", {"name": "arm", "ops": [{"op": "add", "shape": "cylinder", "radius": 3, "height": 12}]}, source="ui")
    scene_tag, _scene = workspace.mesh_answer("scene")
    pieces_tag, _pieces = workspace.mesh_answer("pieces", "rig")
    arm = workspace.document._get("arm")  # noqa: SLF001
    assert counted.viewport == [id(arm.shape)]
    assert counted.tessellate == [id(arm.base)]
    assert scene_tag != tags["scene"]
    assert pieces_tag != tags["pieces"]


def test_a_deleted_part_does_not_keep_its_mesh(rig):
    workspace, _counted, _tags = rig
    workspace.execute("create_part_from_ops", {"name": "spare", "ops": _BOX})
    workspace.mesh_answer("part", "spare")
    workspace.execute("delete_part", {"name": "spare"})
    assert "spare" not in workspace._part_meshes  # noqa: SLF001
    assert ("part", "spare") not in workspace._answers  # noqa: SLF001


def test_pieces_moved_by_the_pose_are_the_assembly_mesh(tmp_path):
    """The page builds the assembly from pieces and a pose. That is the mesh the server would have sent."""
    workspace = Workspace(tmp_path / "rig")
    _rig(workspace.execute)
    workspace.execute("set_joint", {"name": "rig", "joint": "hinge", "value": 30})
    workspace.execute("set_joint", {"name": "rig", "joint": "slide", "value": 7})
    spec, shapes = workspace.document._assembly_view("rig")  # noqa: SLF001
    corners = []
    for instance in placed_instances(spec):
        piece = stored_piece(shapes[instance["part"]])
        matrix = np.array(instance["matrix"])
        vertices = np.array(piece["vertices"]).reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        corners.append(vertices[np.array(piece["triangles"])])
    flat = workspace.document.assembly_mesh("rig")
    assert np.concatenate(corners).ravel() == pytest.approx(np.array(flat["positions"]), abs=2e-3)


# Through the app server, as the page asks.


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
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        _rig(lambda name, arguments: running.workspace.execute(name, arguments, source="ui"))
        yield running
    finally:
        running.shutdown()
        running.server_close()
        if request.param:
            running.workspace.close()


def test_an_unchanged_mesh_is_not_sent_again(server):
    status, tag, body = _get(server, "/api/mesh?scene=1")
    assert status == 200 and tag and json.loads(body)["ok"]
    # The busy check and every event refresh ask again. Nothing changed, so nothing is sent.
    again, same_tag, empty = _get(server, "/api/mesh?scene=1", tag)
    assert (again, same_tag, empty) == (304, tag, b"")
    server.workspace.execute("set_joint", {"name": "rig", "joint": "hinge", "value": 25}, source="ui")
    assert _get(server, "/api/mesh?scene=1", tag)[0] == 304
    server.workspace.execute("place_part", {"name": "frame", "position": [0, 0, 9], "rotation_deg": [0, 0, 0]}, source="ui")
    moved, moved_tag, moved_body = _get(server, "/api/mesh?scene=1", tag)
    assert moved == 200 and moved_tag != tag and moved_body


def test_a_joint_change_sends_the_pose_and_not_the_pieces(server):
    _status, pieces_tag, pieces = _get(server, "/api/mesh?assembly=rig")
    _status, pose_tag, _pose = _get(server, "/api/mesh?pose=rig")
    assert set(json.loads(pieces)["result"]["pieces"]) == {"frame", "arm"}
    server.workspace.execute("set_joint", {"name": "rig", "joint": "slide", "value": 12}, source="ui")
    assert _get(server, "/api/mesh?assembly=rig", pieces_tag)[0] == 304
    status, new_tag, pose = _get(server, "/api/mesh?pose=rig", pose_tag)
    assert status == 200 and new_tag != pose_tag
    arm2 = next(row for row in json.loads(pose)["result"]["instances"] if row["name"] == "arm2")
    assert arm2["matrix"][0][3] == pytest.approx(12)


def test_the_worker_does_not_send_bytes_the_app_already_holds(tmp_path):
    worker = GeometryWorker(tmp_path / "project")
    try:
        _rig(lambda name, arguments: worker.execute(name, arguments, source="ui"))
        replies = []
        real = worker._exchange  # noqa: SLF001

        def exchange(label, method, args):
            result = real(label, method, args)
            replies.append((method, args, "body" in result if isinstance(result, dict) else None))
            return result

        worker._exchange = exchange  # noqa: SLF001
        tag, body = worker.mesh_answer("scene")
        # The same revision is answered here, without asking the worker.
        assert worker.mesh_answer("scene") == (tag, body)
        assert replies == [("mesh_body", ("scene", "", None), True)]
        worker.execute("set_joint", {"name": "rig", "joint": "hinge", "value": 15}, source="ui")
        replies.clear()
        again_tag, again_body = worker.mesh_answer("scene")
        assert (again_tag, again_body) == (tag, body)
        # The worker heard the tag this process holds and sent no bytes back.
        assert [reply[2] for reply in replies] == [False]
        assert json.loads(body)["ok"]
    finally:
        worker.close()


# The page: it sends the tag it holds, and moves the pieces by the pose itself.

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
const PIECES = { assembly: "rig", pieces: { link: {
  vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0], normals: [0, 0, 1, 0, 0, 1, 0, 0, 1], triangles: [0, 1, 2], edges: [[0, 0, 0, 1, 0, 0]] } } };
const moved = (dx) => ({ assembly: "rig", instances: [
  { name: "a", part: "link", matrix: [[1, 0, 0, dx], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], origin_mm: [dx, 0, 0], color: [255, 0, 0] },
  { name: "b", part: "link", matrix: [[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 5], [0, 0, 0, 1]], origin_mm: [0, 0, 5], color: [0, 0, 255] },
] });
let pose = { tag: '"pose-1"', body: moved(10) };
const asked = [];
const parsed = [];
apiFetch = async (url, options) => {
  const held = (options && options.headers && options.headers["If-None-Match"]) || null;
  const answer = url.includes("assembly=") ? { tag: '"pieces-1"', body: PIECES } : pose;
  asked.push([url, held]);
  return {
    status: held === answer.tag ? 304 : 200,
    headers: { get: (name) => (name === "ETag" ? answer.tag : null) },
    json: async () => { parsed.push(url); return { ok: true, result: answer.body }; },
  };
};
ui.page = "assembly";
ui.viewAssembly = "rig";
ui.state = { parts: [], sketches: [], panels: {}, assemblies: [] };
const look = () => ({
  positions: Array.from(ui.mesh.positions),
  normals: Array.from(ui.mesh.normals),
  colors: Array.from(ui.mesh.colors),
  triangleFace: Array.from(ui.mesh.triangle_face),
  faces: ui.mesh.faces.map((face) => face.ref),
  edges: ui.mesh.edges.map((edge) => [edge.ref, edge.points]),
  box: ui.mesh.bounding_box,
  assembly: ui.mesh.assembly,
});
async function probe() {
  const out = {};
  await loadMesh();
  out.first = look();
  const firstMesh = ui.mesh;
  out.firstAsked = asked.splice(0);
  out.firstParsed = parsed.splice(0);
  await loadMesh();
  out.idleAsked = asked.splice(0);
  out.idleParsed = parsed.splice(0);
  out.idleSameMesh = ui.mesh === firstMesh;
  pose = { tag: '"pose-2"', body: moved(20) };
  await loadMesh();
  out.jointAsked = asked.splice(0);
  out.jointParsed = parsed.splice(0);
  out.joint = look();
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
    probe = tmp_path_factory.mktemp("page") / "mesh-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(STATIC / "app.js")], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    report = json.loads(result.stdout)
    assert "error" not in report, report
    return report


def test_the_page_moves_each_piece_by_its_instance(page):
    first = page["first"]
    assert first["assembly"] == "rig"
    # a: moved 10 along X. b: turned a quarter about Z and lifted 5.
    assert first["positions"] == [10, 0, 0, 11, 0, 0, 10, 1, 0, 0, 0, 5, 0, 1, 5, -1, 0, 5]
    assert first["normals"] == [0, 0, 1] * 6
    assert first["colors"] == [255, 0, 0] * 3 + [0, 0, 255] * 3
    assert first["triangleFace"] == [0, 1]
    assert first["faces"] == ["instance/a", "instance/b"]
    assert first["edges"] == [["instance/a", [[10, 0, 0], [11, 0, 0]]], ["instance/b", [[0, 0, 5], [0, 1, 5]]]]
    assert first["box"] == {"min": [-1, 0, 0], "max": [11, 1, 5]}


def test_the_page_sends_its_tags_and_parses_nothing_unchanged(page):
    assert page["firstAsked"] == [["/api/mesh?assembly=rig", None], ["/api/mesh?pose=rig", None]]
    assert page["idleAsked"] == [["/api/mesh?assembly=rig", '"pieces-1"'], ["/api/mesh?pose=rig", '"pose-1"']]
    assert page["idleParsed"] == []
    assert page["idleSameMesh"] is True


def test_after_a_joint_change_the_page_reads_only_the_pose(page):
    assert page["jointParsed"] == ["/api/mesh?pose=rig"]
    assert page["joint"]["positions"][:9] == [20, 0, 0, 21, 0, 0, 20, 1, 0]
