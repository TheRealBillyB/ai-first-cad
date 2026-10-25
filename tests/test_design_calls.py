"""In-process document calls remove their temporary directory when the call returns."""

from __future__ import annotations

import tempfile
from pathlib import Path


def test_in_process_call_directory_is_removed_after_each_call():
    from aicad.sandbox_run import script_document

    class Doc:
        def measure(self):
            return {"volume_mm3": 1}

    before = {path.resolve() for path in Path(tempfile.gettempdir()).glob("aicad-calls-*") if path.exists()}
    calls: list[dict] = []
    proxy = script_document(Doc(), calls)
    assert proxy.measure()["volume_mm3"] == 1
    assert proxy.measure()["volume_mm3"] == 1
    left = [
        path
        for path in Path(tempfile.gettempdir()).glob("aicad-calls-*")
        if path.exists() and path.resolve() not in before
    ]
    assert left == []
    assert calls == [
        {"method": "measure", "result": {"volume_mm3": 1}},
        {"method": "measure", "result": {"volume_mm3": 1}},
    ]
