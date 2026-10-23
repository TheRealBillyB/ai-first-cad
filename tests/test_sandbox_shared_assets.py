"""Shared geometry caches and font sidecars must not leave the project.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, outside the project.
Opening the project, reloading a cached solid, meshing, solving, and drawing
must not create it. A font path in the project must not be followed when it
points outside.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.fea import _parse_frd, fea_available
from aicad.kernel import export_step_file, make_from_ops
from aicad.sandbox import run_part_script, sandbox_supported

_BOX = [{"op": "add", "shape": "box", "size": [4, 4, 4]}]
_CANARY_NAME = "SANDBOX_CANARY.txt"
_FONT_NAME = "SANDBOX_CANARY.ttf"


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def _canary(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return directory / _CANARY_NAME


def _assert_canary_absent(path: Path) -> None:
    assert not path.exists()


def _writer(path: Path, canary: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from pathlib import Path\n"
        f"Path({str(canary)!r}).write_text('CANARY_HIT\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )


class _Bomb:
    def __init__(self, canary: Path):
        self.canary = str(canary)

    def __reduce__(self):
        statement = (
            "__import__('pathlib').Path(%r).write_text('CANARY_HIT\\n', encoding='utf-8')"
            % self.canary
        )
        return eval, (statement,)  # noqa: S307 - this runs only if a loader unpickles the file


def _pickle_bytes(canary: Path) -> bytes:
    return pickle.dumps(_Bomb(canary), protocol=pickle.HIGHEST_PROTOCOL)


def _marker_font(path: Path) -> None:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((400, 700))
    pen.lineTo((400, 0))
    pen.closePath()
    glyph = pen.glyph()
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({65: "A"})
    builder.setupGlyf({".notdef": glyph, "A": glyph})
    builder.setupHorizontalMetrics({".notdef": (500, 50), "A": (500, 50)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "SandboxCanary", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    path.parent.mkdir(parents=True, exist_ok=True)
    builder.save(str(path))


def _script_hash(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


def _session(project: Path, geometry: str, script: str) -> None:
    payload = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "cached",
                "source": "script",
                "script": script,
                "geometry_file": geometry,
                "script_sha256": _script_hash(script),
            }
        ],
    }
    project.mkdir(parents=True, exist_ok=True)
    (project / "session.json").write_text(json.dumps(payload), encoding="utf-8")


def test_cached_geometry_outside_symlink_is_not_imported(tmp_path, config_home):
    outside = tmp_path / "outside"
    canary = _canary(outside)
    step = outside / "hidden.step"
    export_step_file(make_from_ops(_BOX), step)

    project = tmp_path / "project"
    link = project / ".aicad" / "geometry" / "cached.step"
    link.parent.mkdir(parents=True)
    link.symlink_to(step)
    script = "part = Box(4, 4, 4)\n"
    _session(project, ".aicad/geometry/cached.step", script)
    opened = Document(project)
    info = opened.list_parts()[0]
    assert info.built is False
    assert any("outside" in problem for problem in opened.problems)
    _assert_canary_absent(canary)

    nested = tmp_path / "nested"
    folder = nested / ".aicad" / "geometry"
    folder.parent.mkdir(parents=True)
    try:
        folder.symlink_to(step.parent, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory symlink")
    _session(nested, ".aicad/geometry/hidden.step", script)
    again = Document(nested)
    assert again.list_parts()[0].built is False
    assert any("outside" in problem for problem in again.problems)
    _assert_canary_absent(canary)


def test_serialized_cache_is_not_executed_when_a_project_opens(tmp_path, config_home):
    outside = tmp_path / "outside"
    canary = _canary(outside)
    project = tmp_path / "project"
    blob = _pickle_bytes(canary)
    geometry = project / ".aicad" / "geometry" / "cached.step"
    geometry.parent.mkdir(parents=True)
    geometry.write_bytes(blob)
    cache = project / ".aicad" / "cache"
    cache.mkdir(parents=True)
    (cache / "mesh.pkl").write_bytes(blob)
    (cache / "mesh.cloudpickle").write_bytes(blob)
    import joblib
    import numpy as np

    joblib.dump(_Bomb(canary), cache / "mesh.joblib")
    np.save(cache / "mesh.npy", np.array(_Bomb(canary), dtype=object))
    (project / "mesh.msh").write_bytes(blob)
    _session(project, ".aicad/geometry/cached.step", "part = Box(1, 1, 1)\n")
    opened = Document(project)
    assert opened.list_parts()[0].built is False
    from aicad.workspace import Workspace

    Workspace(tmp_path / "here").execute("open_project", {"path": str(project)})
    _assert_canary_absent(canary)


def test_planted_calculix_files_are_not_run_with_a_solve(tmp_path, config_home):
    outside = tmp_path / "outside"
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    _writer(project / "write_canary.py", canary)
    deck = "\n".join(
        [
            "*INCLUDE, INPUT=" + str(project / "write_canary.py"),
            "*INCLUDE, INPUT=" + str(outside / "missing.inp"),
            "*HEADING",
            "planted",
        ]
    )
    (project / "job.inp").write_text(deck + "\n", encoding="utf-8")
    token = "CANARY_FRD_UNREAD\n"
    (project / "job.frd").write_text(token, encoding="utf-8")
    (project / "job.dat").write_text(token, encoding="utf-8")
    document = Document(project)
    document.create_part_from_ops("box", _BOX)
    try:
        _parse_frd(project / "job.frd", 1)
    except Exception:
        pass
    available, _reason = fea_available()
    if available:
        document.solve_mechanics(
            "box",
            {
                "material": "steel",
                "mesh_size_mm": 4.0,
                "fixes": [{"name": "root", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
                "loads": [
                    {
                        "name": "tip",
                        "type": "force",
                        "vector": [0, 0, -10],
                        "select": {"kind": "plane", "normal": [1, 0, 0]},
                    }
                ],
            },
        )
    assert (project / "job.frd").read_text(encoding="utf-8") == token
    assert (project / "job.dat").read_text(encoding="utf-8") == token
    assert not (project / "job.sta").exists()
    _assert_canary_absent(canary)


def test_project_gmsh_sidecar_is_not_run_while_meshing(tmp_path, monkeypatch):
    try:
        import gmsh  # noqa: F401
    except (ImportError, OSError) as exc:
        pytest.skip(f"gmsh is not usable here: {exc}")
    outside = tmp_path / "outside"
    canary = _canary(outside)
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    writer = project / "write_canary.py"
    _writer(writer, canary)
    command = f'System "python3 {writer}";\n'
    (project / ".gmshrc").write_text(command, encoding="utf-8")
    (project / ".gmsh-options").write_text(command, encoding="utf-8")
    (project / "part.geo").write_text(command + 'SetFactory("OpenCASCADE");\n', encoding="utf-8")
    (project / "part.msh").write_text(command, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(project)
    from aicad.fea import _mesh_attempt

    mesh = _mesh_attempt(make_from_ops(_BOX), 2.0, 1, algorithm=1, optimize=0, occ_fix=False)
    assert mesh["element_count"] > 0
    _assert_canary_absent(canary)
    assert not (home / ".gmshrc").exists()
    assert not (home / ".gmsh-options").exists()


def test_shared_font_config_does_not_open_an_outside_font(tmp_path):
    outside = tmp_path / "outside"
    canary = _canary(outside)
    font = outside / "fonts" / _FONT_NAME
    _marker_font(font)
    project = tmp_path / "project"
    project.mkdir()
    linked = project / "fonts"
    linked.symlink_to(font.parent, target_is_directory=True)
    _writer(project / "write_canary.py", canary)
    _writer(project / "canary_backend.py", canary)
    (project / "matplotlibrc").write_text("backend: module://canary_backend\n", encoding="utf-8")
    (project / "style.mplstyle").write_text(f"font.sans-serif: {font}\n", encoding="utf-8")
    (project / "fonts.conf").write_text(
        "<?xml version='1.0'?>\n<fontconfig><dir>" + str(font.parent) + "</dir></fontconfig>\n",
        encoding="utf-8",
    )
    ini = "\n".join(
        [
            "[core]",
            "SUPPORT_DIRS = " + str(font.parent),
            "    " + str(linked),
            "[odafc-addon]",
            "UNIX_EXEC_PATH = python3 " + str(project / "write_canary.py"),
            "[openscad-addon]",
            "UNIX_EXEC_PATH = python3 " + str(project / "write_canary.py"),
            "[browse-command]",
            "TEXT_EDITOR = python3 " + str(project / "write_canary.py") + " {filename}",
            "",
        ]
    )
    (project / "ezdxf.ini").write_text(ini, encoding="utf-8")
    cache = tmp_path / "cache"
    config = tmp_path / "xdg-config"
    env = os.environ.copy()
    # Keep HOME so this interpreter still finds its installed packages.
    # The font cache and ezdxf's own config go to empty temporary directories.
    env["XDG_CACHE_HOME"] = str(cache)
    env["XDG_CONFIG_HOME"] = str(config)
    env["AICAD_CONFIG"] = str(tmp_path / "aicad-config")
    code = (
        "import json, os\n"
        "from pathlib import Path\n"
        f"os.chdir({str(project)!r})\n"
        "from aicad.document import Document\n"
        "from aicad.workspace import Workspace\n"
        "Document(Path(os.getcwd()))\n"
        f"Workspace(Path({str(tmp_path / 'here')!r})).execute('open_project', {{'path': os.getcwd()}})\n"
        "print(json.dumps({'cwd': os.getcwd()}))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["cwd"] == str(project)
    blob = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in cache.rglob("*")
        if path.is_file()
    )
    combined = done.stdout + done.stderr + blob
    assert _FONT_NAME not in combined
    assert "SandboxCanary" not in combined
    _assert_canary_absent(canary)


def test_drawing_fonts_do_not_follow_a_project_symlink(tmp_path, config_home, monkeypatch):
    outside = tmp_path / "outside"
    canary = _canary(outside)
    font = outside / _FONT_NAME
    _marker_font(font)
    project = tmp_path / "project"
    project.mkdir()
    (project / "DejaVuSans.ttf").symlink_to(font)
    (project / "DejaVuSans.otf").symlink_to(font)
    (project / "style.mplstyle").write_text(f"font.sans-serif: {font}\n", encoding="utf-8")
    _writer(project / "canary_backend.py", canary)
    (project / "matplotlibrc").write_text("backend: module://canary_backend\n", encoding="utf-8")
    monkeypatch.chdir(project)
    document = Document(project)
    document.create_part_from_ops("box", _BOX)
    document.render("box")
    from aicad.bom import _font_for, render_table
    from aicad.drawing import _font as drawing_font
    from aicad.honesty import _SANS, _font as honesty_font, write_result_sheet
    from aicad.plan_sheet import _font as plan_font, render_plan_sheet
    from aicad.render import _font as render_font

    faces = [drawing_font(14), render_font(14), _font_for(14, "box"), honesty_font(_SANS, 14)]
    plan_font(_SANS[0], 14)
    render_table(
        {
            "assembly": "box",
            "rows": [
                {
                    "item": "1",
                    "quantity": 1,
                    "category": "part",
                    "name": "box",
                    "description": "box",
                    "mass_estimate_kg": None,
                }
            ],
            "result_what": "count",
        },
        project / "bom.png",
    )
    write_result_sheet(
        project / "result.png",
        {"result_label": "Result", "result_what": "A check."},
        title="Result",
    )
    render_plan_sheet(
        project / "plan.png",
        title="Plan",
        revision=1,
        status="draft",
        panels=[{"title": "View", "notes": ["Note"]}],
    )
    for face in faces:
        loaded = getattr(face, "path", None)
        if loaded:
            assert Path(loaded).resolve() != font.resolve()
    _assert_canary_absent(canary)


def test_sandboxed_part_does_not_apply_shipped_font_config(tmp_path, config_home):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    outside = tmp_path / "outside"
    canary = _canary(outside)
    font = outside / "fonts" / _FONT_NAME
    _marker_font(font)
    project = tmp_path / "shared"
    project.mkdir()
    (project / "fonts").symlink_to(font.parent, target_is_directory=True)
    (project / "ezdxf.ini").write_text(
        "[core]\nSUPPORT_DIRS = " + str(font.parent) + "\n",
        encoding="utf-8",
    )
    _writer(project / "write_canary.py", canary)
    (project / ".gmshrc").write_text(
        f'System "python3 {project / "write_canary.py"}";\n',
        encoding="utf-8",
    )
    script = "import build123d\npart = Box(1, 1, 1)\n"
    shape = run_part_script(script, project)
    from aicad.kernel import measure

    assert measure(shape).volume_mm3 == pytest.approx(1)
    _assert_canary_absent(canary)
