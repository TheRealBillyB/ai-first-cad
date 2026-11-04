"""Two apps on one project each keep a copy, so the second one says so."""

from pathlib import Path

import pytest

from aicad.app import _open_project_path, make_server


@pytest.fixture(autouse=True)
def _records(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "servers"))


def _server(project: Path):
    return make_server(project, port=0)


def test_the_second_app_on_a_project_names_the_first(tmp_path):
    first = _server(tmp_path / "project")
    second = _server(tmp_path / "project")
    try:
        assert first.project_also_open_at(first.workspace.root) is None
        assert second.project_also_open_at(second.workspace.root) == first.server_address[1]
    finally:
        second.server_close()
        first.server_close()


def test_another_project_is_not_a_conflict(tmp_path):
    first = _server(tmp_path / "one")
    second = _server(tmp_path / "two")
    try:
        assert second.project_also_open_at(second.workspace.root) is None
    finally:
        second.server_close()
        first.server_close()


def test_a_closed_app_leaves_nothing_behind(tmp_path):
    first = _server(tmp_path / "project")
    port = first.server_address[1]
    first.server_close()
    assert not _open_project_path(port).exists()
    second = _server(tmp_path / "project")
    try:
        assert second.project_also_open_at(second.workspace.root) is None
    finally:
        second.server_close()


def test_a_crashed_app_record_is_removed(tmp_path):
    first = _server(tmp_path / "project")
    port = first.server_address[1]
    root = str(first.workspace.root)
    first.server_close()
    record = _open_project_path(port)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(root, encoding="utf-8")
    second = _server(tmp_path / "project")
    try:
        assert second.project_also_open_at(second.workspace.root) is None
        assert not record.exists()
    finally:
        second.server_close()


def test_opening_another_project_checks_again(tmp_path):
    first = _server(tmp_path / "one")
    second = _server(tmp_path / "two")
    try:
        assert second.project_also_open_at(second.workspace.root) is None
        assert second.project_also_open_at(first.workspace.root) == first.server_address[1]
    finally:
        second.server_close()
        first.server_close()
