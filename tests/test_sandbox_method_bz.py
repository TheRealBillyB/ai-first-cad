"""A name with a line separator stays off drawings, bills, and reports.

The marker is CANARY_HIT. A line separator or a paragraph separator in a
name does not open another line, and that marker stays where it was written.
"""

from __future__ import annotations

import json

import pytest

from aicad.bom import write_csv
from aicad.document import Document
from aicad.drawing import _Text, _write_dxf
from aicad.errors import GeometryError
from aicad.kinematics import normalize

_MARKER = "CANARY_HIT"
_LINE = "\u2028"
_PARA = "\u2029"


def _assembly(name: str = "arm", instance: str = "base", material: str = "steel") -> dict:
    return {
        "name": name,
        "instances": [{"name": instance, "part": "base", "material": material}],
        "joints": [],
    }


def test_a_line_separator_in_a_name_is_refused():
    for name in (f"arm{_LINE}*INCLUDE", f"arm{_PARA}next", "arm\n*INCLUDE", "arm\tnext"):
        with pytest.raises(GeometryError, match="one line"):
            normalize(_assembly(name=name))
    with pytest.raises(GeometryError, match="one line"):
        normalize(_assembly(instance=f"base{_LINE}next"))
    with pytest.raises(GeometryError, match="one line"):
        normalize(_assembly(material=f"steel{_LINE}*INCLUDE, INPUT=secret"))
    kept = normalize(_assembly(instance="支架"))
    assert kept["instances"][0]["name"] == "支架"


def test_a_session_name_with_a_line_separator_is_not_stored(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    (project / "session.json").write_text(
        json.dumps(
            {
                "parts": [{"name": f"block{_LINE}next", "source": "ops", "ops": []}],
                "sketches": [{"name": f"sketch{_PARA}next", "spec": {}}],
                "assemblies": [_assembly(name=f"arm{_LINE}*INCLUDE")],
            }
        ),
        encoding="utf-8",
    )
    document = Document(project)
    names = [part.name for part in document.list_parts()]
    assert all(_LINE not in name and _PARA not in name for name in names)
    assert f"block{_LINE}next" not in names
    assert all(_LINE not in name for name in document._assemblies)  # noqa: SLF001
    text = json.dumps(document.session_payload())
    assert _LINE not in text
    assert "*INCLUDE" not in text
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"


def test_a_drawing_and_a_bill_keep_a_separator_on_one_line(tmp_path):
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    dxf = tmp_path / "sheet.dxf"
    plain = tmp_path / "plain.dxf"
    _write_dxf(plain, [], [_Text(10, 10, "bracket", 3.0, "TITLE", (0, 0, 0))])
    _write_dxf(dxf, [], [_Text(10, 10, f"part{_LINE}0{_PARA}LINE{_LINE}CANARY_HIT", 3.0, "TITLE", (0, 0, 0))])
    plain_lines = plain.read_text(encoding="utf-8").splitlines()
    nasty_lines = dxf.read_text(encoding="utf-8").splitlines()
    assert len(plain_lines) == len(nasty_lines)
    assert nasty_lines.count("TEXT") == 1
    assert _LINE not in dxf.read_text(encoding="utf-8")
    assert _PARA not in dxf.read_text(encoding="utf-8")
    csv_path = tmp_path / "bom.csv"
    write_csv(
        {"rows": [{"name": f"{_LINE}=1+1", "description": f"note{_PARA}*INCLUDE", "material": "steel"}], "assembly": f"arm{_LINE}next"},
        csv_path,
    )
    body = csv_path.read_text(encoding="utf-8-sig")
    assert _LINE not in body
    assert _PARA not in body
    assert "\n*INCLUDE" not in body
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
