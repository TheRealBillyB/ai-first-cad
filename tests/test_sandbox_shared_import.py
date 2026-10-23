"""Shared parts, bills of materials, and drawings stay inside the project.

The canary is one file, SANDBOX_CANARY.txt, containing CANARY_HIT. Pasting or
dropping a part, a bill-of-materials row, or a URL must not create it outside
the project, and must not open a network connection. A STEP file is read as
itself only. A name in it that leaves its folder is refused. A STEP, SVG, or
drawing that names an address does not open a connection.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import quote

import pytest
from build123d import Box, export_step
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPConstruct import STEPConstruct_ExternRefs
from OCP.STEPControl import STEPControl_Reader
from OCP.StepBasic import StepBasic_ProductDefinition

from aicad.app import make_server
from aicad.document import Document
from aicad.errors import GeometryError
from aicad.workspace import Workspace

_CANARY_NAME = "SANDBOX_CANARY.txt"
_CANARY_TEXT = "CANARY_HIT\n"
_SECRET = "OUTSIDE_SECRET\n"
_OPS = [{"op": "add", "shape": "box", "size": [2, 2, 2]}]


class _Watch:
    """A project, a directory outside it, and a local socket that must stay idle."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.project = tmp_path / "project"
        self.project.mkdir()
        self.outside = tmp_path / "outside"
        self.outside.mkdir()
        self.canary = self.outside / _CANARY_NAME
        self.secret = self.outside / "secret.txt"
        self.secret.write_text(_SECRET, encoding="utf-8")
        self.hidden = self.outside / "hidden.step"
        export_step(Box(10, 10, 10), self.hidden)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.sock.settimeout(0.3)

    def close(self) -> None:
        self.sock.close()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/{name}"

    def assert_quiet(self) -> None:
        assert not self.canary.exists()
        assert self.secret.read_text(encoding="utf-8") == _SECRET
        assert not any(self.project.rglob(_CANARY_NAME))
        for path in self.project.rglob("*"):
            if path.is_file():
                blob = path.read_bytes()
                assert b"OUTSIDE_SECRET" not in blob
        try:
            connection, _address = self.sock.accept()
        except TimeoutError:
            return
        connection.close()
        raise AssertionError("shared content opened a network connection")


@pytest.fixture
def watch(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    holder = _Watch(tmp_path)
    try:
        yield holder
    finally:
        holder.close()


def _craft_step(base: Path, dest: Path, reference: str) -> None:
    """A valid STEP part that also names ``reference`` as another file."""
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(base)) == IFSelect_RetDone
    model = reader.WS().Model()
    product = next(
        model.Value(index)
        for index in range(1, model.NbEntities() + 1)
        if isinstance(model.Value(index), StepBasic_ProductDefinition)
    )
    refs = STEPConstruct_ExternRefs(reader.WS())
    refs.Init(reader.WS())
    refs.AddExternRef(reference, product, "STEP AP214")
    refs.WriteExternRefs(214)
    assert reader.WS().WriteFile(str(dest)) == IFSelect_RetDone


def _base_step(directory: Path) -> Path:
    path = directory / "base.step"
    export_step(Box(2, 2, 2), path)
    return path


def _session(project: Path, parts: list, assemblies: list | None = None) -> None:
    payload = {"units": "mm", "sketches": [], "parts": parts, "assemblies": assemblies or []}
    (project / "session.json").write_text(json.dumps(payload), encoding="utf-8")


def test_dropped_step_does_not_read_or_write_outside_the_project(watch: _Watch):
    base = _base_step(watch.root)
    dropped = watch.outside / "dropped.step"
    _craft_step(base, dropped, str(watch.hidden))
    _craft_step(base, watch.outside / "also.step", str(watch.canary))
    hidden_before = watch.hidden.read_bytes()
    document = Document(watch.project)
    with pytest.raises(GeometryError, match="outside its folder"):
        document.import_step("dropped", dropped)
    with pytest.raises(GeometryError, match="outside its folder"):
        document.import_step("canaryref", watch.outside / "also.step")
    assert document.list_parts() == []
    assert not (watch.project / "imports").exists()
    assert watch.hidden.read_bytes() == hidden_before
    reopened = Document(watch.project)
    assert reopened.list_parts() == []
    watch.assert_quiet()


def test_opening_a_project_ignores_a_relative_step_reference(watch: _Watch):
    imports = watch.project / "imports"
    imports.mkdir()
    base = _base_step(imports)
    shared = imports / "shared.step"
    _craft_step(base, shared, "../../outside/hidden.step")
    hidden_before = watch.hidden.read_bytes()
    _session(watch.project, [{"name": "shared", "source": "step", "step_file": "imports/shared.step"}])
    document = Document(watch.project)
    assert document.list_parts()[0].built is False
    assert any("outside its folder" in problem for problem in document.problems)
    home = Workspace(watch.root / "here")
    opened = home.open_project(watch.project)
    assert opened["parts"][0]["name"] == "shared"
    assert home.document.list_parts()[0].built is False
    assert watch.hidden.read_bytes() == hidden_before
    watch.assert_quiet()


def test_step_http_and_file_references_are_not_opened(watch: _Watch):
    incoming = watch.root / "incoming"
    incoming.mkdir()
    base = _base_step(incoming)
    http_step = incoming / "remote.step"
    file_step = incoming / "fileurl.step"
    _craft_step(base, http_step, watch.url("hidden.step"))
    _craft_step(base, file_step, watch.hidden.as_uri())
    document = Document(watch.project)
    info = document.import_step("remote", http_step)
    assert info.built is True
    assert document.measure("remote").volume_mm3 == pytest.approx(8)
    # A file URI is not fetched. On Windows the STEP writer stores it as a
    # drive path, and a drive path is not read at all.
    if sys.platform == "win32":
        with pytest.raises(GeometryError, match="outside its folder"):
            document.import_step("fileurl", file_step)
    else:
        info = document.import_step("fileurl", file_step)
        assert info.built is True
        assert document.measure("fileurl").volume_mm3 == pytest.approx(8)
    watch.assert_quiet()


def test_pasted_part_script_is_not_run_on_open_or_replace(watch: _Watch):
    script = (
        "import subprocess, sys\n"
        "code = (\n"
        "    'from pathlib import Path\\n'\n"
        f"    'Path({str(watch.canary)!r}).write_text({_CANARY_TEXT!r}, encoding=\"utf-8\")\\n'\n"
        ")\n"
        "subprocess.run([sys.executable, '-c', code])\n"
        "part = Box(1, 1, 1)\n"
    )
    _session(watch.project, [{"name": "pasted", "source": "script", "script": script}])
    document = Document(watch.project)
    assert document.list_parts()[0].built is False
    document.replace_session(
        {
            "units": "mm",
            "sketches": [],
            "assemblies": [],
            "parts": [{"name": "pasted", "source": "script", "script": script}],
        }
    )
    assert document.list_parts()[0].built is False
    home = Workspace(watch.root / "here")
    home.execute("open_project", {"path": str(watch.project)})
    assert home.document.list_parts()[0].built is False
    watch.assert_quiet()


def test_a_dropped_url_is_not_fetched_as_a_part(watch: _Watch):
    document = Document(watch.project)
    with pytest.raises(GeometryError):
        document.import_step("fromurl", watch.url("part.step"))
    with pytest.raises(GeometryError):
        document.import_step("fromfile", watch.hidden.as_uri())
    home = Workspace(watch.project)
    with pytest.raises(GeometryError):
        home.execute("import_step", {"name": "again", "path": watch.url("part.step")})
    assert document.list_parts() == []
    watch.assert_quiet()


def test_a_shared_bom_row_does_not_write_outside_or_connect(watch: _Watch):
    material = f"{watch.url('bom.csv')} =cmd|CANARY_HIT file://{watch.canary}"
    _session(
        watch.project,
        [{"name": "base", "source": "ops", "ops": _OPS}],
        [
            {
                "name": "stand",
                "instances": [{"name": "base", "part": "base", "material": material}],
                "joints": [],
            },
            {
                "name": "asm/../../outside/SANDBOX_CANARY.txt",
                "instances": [{"name": "base", "part": "base", "material": material}],
                "joints": [],
            },
        ],
    )
    document = Document(watch.project)
    report = document.bill_of_materials("stand")
    csv_path = Path(report["csv"])
    assert csv_path.is_file()
    assert watch.project in csv_path.parents
    with pytest.raises(GeometryError):
        document.bill_of_materials("asm/../../outside/SANDBOX_CANARY.txt")
    watch.assert_quiet()


def test_a_research_url_in_a_shared_plan_is_stored_as_text(watch: _Watch):
    home = Workspace(watch.project)
    home.execute("start_plan", {"name": "shelf", "title": "Shelf"})
    home.execute(
        "add_plan_research",
        {
            "name": "shelf",
            "note": "A pasted catalogue page, kept as text.",
            "source": watch.url("catalogue"),
            "title": "Catalogue",
        },
    )
    opened = Workspace(watch.root / "here")
    opened.open_project(watch.project)
    plan = opened.execute("get_plan", {"name": "shelf"})
    sources = [item["source"] for item in plan["revisions"][-1]["research"]]
    assert sources == [watch.url("catalogue")]
    watch.assert_quiet()


def test_svg_and_drawing_references_are_not_followed(watch: _Watch):
    svg = watch.project / "shared.svg"
    svg.write_text(
        "\n".join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">',
                f'<image href="{watch.hidden.as_uri()}" xlink:href="{watch.url("image.png")}"/>',
                f'<script href="{watch.url("draw.js")}"></script>',
                "</svg>",
            ]
        ),
        encoding="utf-8",
    )
    drawing = watch.project / "shared.dxf"
    drawing.write_text(
        "\n".join(
            [
                "0",
                "SECTION",
                "2",
                "OBJECTS",
                "0",
                "IMAGEDEF",
                "1",
                watch.url("sheet.png"),
                "1",
                watch.hidden.as_uri(),
                "1",
                str(watch.canary),
                "0",
                "ENDSEC",
                "0",
                "EOF",
                "",
            ]
        ),
        encoding="utf-8",
    )
    document = Document(watch.project)
    for name, path in (("svgpart", svg), ("drawing", drawing)):
        with pytest.raises(GeometryError):
            document.import_step(name, path)
    assert document.list_parts() == []

    server = make_server(watch.project, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        connection = HTTPConnection(host, port, timeout=30)
        query = "/api/file?path=" + quote("shared.svg") + "&token=" + quote(server.page_token)
        connection.request("GET", query)
        response = connection.getresponse()
        body = response.read()
        status = response.status
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    assert status == 200
    assert b"OUTSIDE_SECRET" not in body
    assert b"CANARY_HIT" not in body
    watch.assert_quiet()
