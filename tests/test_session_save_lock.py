"""Two saves of one project at the same moment do not lose either.

Each copy of the project checked that session.json had not changed since it
read it, then wrote it. Both checks could pass before either wrote, and the
second write put back a part the first had deleted. The check and the write
now happen under the operating system's lock on ``.aicad/session.lock``.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from aicad import document as document_module
from aicad.document import Document
from aicad.errors import DocumentError

_BOX = [{"op": "add", "shape": "box", "size": [10, 10, 10]}]


def _saved_parts(root) -> list[str]:
    payload = json.loads((root / "session.json").read_text(encoding="utf-8"))
    return sorted(part["name"] for part in payload["parts"])


def test_a_save_that_starts_while_another_is_writing_waits_and_then_refuses(tmp_path, monkeypatch):
    first = Document(tmp_path)
    first.create_part_from_ops("a", _BOX)
    first.create_part_from_ops("b", _BOX)
    second = Document(tmp_path)

    import aicad.paths

    real_publish = aicad.paths.publish_text
    writing = threading.Event()
    slow_thread: list[int] = []

    def slow_publish(*args, **kwargs):
        if threading.get_ident() in slow_thread:
            writing.set()
            # The other save starts now, while this one is between its check and its write.
            time.sleep(0.5)
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(aicad.paths, "publish_text", slow_publish)
    errors: list[BaseException] = []

    def delete_b() -> None:
        slow_thread.append(threading.get_ident())
        try:
            first.delete_part("b")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=delete_b)
    worker.start()
    assert writing.wait(10)
    with pytest.raises(DocumentError, match="changed by another program"):
        second.create_part_from_ops("c", _BOX)
    worker.join(10)
    assert errors == []
    # The delete was saved and nothing put b back.
    assert _saved_parts(tmp_path) == ["a"]


def test_a_save_that_cannot_get_the_lock_says_so(tmp_path, monkeypatch):
    doc = Document(tmp_path)
    monkeypatch.setattr(document_module, "_SESSION_LOCK_SECONDS", 0.2)
    with document_module._session_write_lock(tmp_path):
        held = threading.Event()
        caught: list[BaseException] = []

        def save() -> None:
            try:
                doc.create_part_from_ops("x", _BOX)
            except BaseException as exc:  # noqa: BLE001
                caught.append(exc)
            held.set()

        thread = threading.Thread(target=save)
        thread.start()
        assert held.wait(10)
        thread.join(10)
    assert caught and "still saving it" in str(caught[0])
    # Once the lock is free, a save goes through.
    doc.create_part_from_ops("y", _BOX)
    assert "y" in _saved_parts(tmp_path)


def test_the_lock_file_is_a_plain_file_in_the_project_folder(tmp_path):
    Document(tmp_path).create_part_from_ops("a", _BOX)
    lock = tmp_path / ".aicad" / "session.lock"
    assert lock.is_file() and not lock.is_symlink()
