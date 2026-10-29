"""A script can heal the solid it built."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.mesh import heal
from aicad.sandbox import allow_design_scripts, run_design_script, run_part_script, sandbox_supported


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def test_heal_accepts_the_solid_a_script_builds():
    from build123d import Box

    from aicad.kernel import Shape, make_from_ops

    healed, note, changed = heal(Box(10, 10, 10), 0.1)
    assert "Sewed and fixed" in note
    assert changed is False
    assert healed.impl.volume == pytest.approx(1000)
    same, _note, _changed = heal(Shape(Box(4, 4, 4)), 0.1)
    assert same.impl.volume == pytest.approx(64)
    kept = make_from_ops([{"op": "add", "shape": "box", "size": [2, 2, 2]}])
    again, _note, _changed = heal(kept, 0.1)
    assert again.impl.volume == pytest.approx(8)
    with pytest.raises(GeometryError, match="needs a solid"):
        heal(object(), 0.1)


def test_a_part_script_can_heal_the_solid_it_built(tmp_path: Path, config_home):
    _sandbox_or_skip()
    shape = run_part_script(
        textwrap.dedent(
            """
            from aicad.mesh import heal
            healed, note, changed = heal(Box(10, 10, 10), 0.1)
            part = healed.impl
            """
        ),
        tmp_path,
    )
    assert shape.impl.volume == pytest.approx(1000)


def test_a_design_script_can_heal_the_solid_it_built(tmp_path: Path, config_home):
    _sandbox_or_skip()
    project = tmp_path / "project"
    project.mkdir()
    allow_design_scripts(project)
    payload = run_design_script(
        project,
        textwrap.dedent(
            """
            from build123d import Box
            from aicad.mesh import heal
            healed, note, changed = heal(Box(10, 10, 10), 0.1)
            print(note)
            print("VOL", float(healed.impl.volume))
            """
        ),
    )
    assert payload["ok"] is True, payload.get("error")
    assert "Sewed and fixed" in payload["stdout"]
    volume = next(float(line.split()[1]) for line in payload["stdout"].splitlines() if line.startswith("VOL "))
    assert volume == pytest.approx(1000)
