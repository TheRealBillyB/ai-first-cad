"""In-process document calls remove their temporary directory when the call returns."""

from __future__ import annotations

from pathlib import Path


def test_in_process_call_directory_is_removed_after_each_call(monkeypatch):
    from aicad import paths
    from aicad.sandbox_run import script_document

    class Doc:
        def measure(self):
            return {"volume_mm3": 1}

    opened: list[Path] = []
    real = paths.open_held_directory

    def record(prefix: str = "aicad-tool-") -> Path:
        folder = real(prefix)
        if str(prefix).startswith("aicad-calls-"):
            opened.append(Path(folder))
        return folder

    monkeypatch.setattr(paths, "open_held_directory", record)
    calls: list[dict] = []
    proxy = script_document(Doc(), calls)
    assert proxy.measure()["volume_mm3"] == 1
    assert proxy.measure()["volume_mm3"] == 1
    assert len(opened) == 2
    assert [path for path in opened if path.exists()] == []
    assert calls == [
        {"method": "measure", "result": {"volume_mm3": 1}},
        {"method": "measure", "result": {"volume_mm3": 1}},
    ]
