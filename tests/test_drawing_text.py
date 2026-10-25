"""DXF text stays one group value. A title cannot start another entity."""

from aicad.drawing import _Text, _write_dxf


def test_dxf_text_cannot_start_another_entity(tmp_path):
    path = tmp_path / "sheet.dxf"
    nasty = "TITLE)\n0\nLINE\n8\nHACK\n10\n0\n20\n0\n11\n9\n21\n9"
    _write_dxf(path, [], [_Text(10, 10, nasty, 2.5, "TITLE", (0, 0, 0))])
    raw = path.read_text(encoding="utf-8")
    assert "\nLINE\n" not in raw
    import ezdxf

    loaded = ezdxf.readfile(path)
    lines = list(loaded.modelspace().query("LINE"))
    texts = [entity.dxf.text for entity in loaded.modelspace().query("TEXT")]
    assert lines == []
    assert len(texts) == 1
    assert "LINE" not in texts[0].split("\\n")[0]
    assert "\n" not in texts[0]
    assert "\r" not in texts[0]
