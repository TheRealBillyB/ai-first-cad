"""The workspace file view does not open a link or a file with another name.

The marker is CANARY_HIT outside the project. A request for a project path
that names that file must not return the marker, and must not change it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aicad.app import make_server

_CANARY = b"CANARY_HIT\n"


def _link_dir(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("this machine cannot create a junction")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory symlink")


def _get(host: str, port: int, path: str, token: str) -> tuple[int, bytes]:
    connection = HTTPConnection(host, port, timeout=30)
    connection.request(
        "GET",
        path,
        headers={"Host": f"127.0.0.1:{port}", "X-Aicad-Token": token},
    )
    response = connection.getresponse()
    body = response.read()
    status = response.status
    connection.close()
    return status, body


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_APPROVALS", str(tmp_path / "approvals.json"))
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "note.txt").write_bytes(b"a plain note\n")
    return folder


def _serve(project: Path):
    server = make_server(project, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_a_workspace_file_with_another_name_is_not_shown(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_bytes(_CANARY)
    try:
        os.link(secret, project / "alias.txt")
    except OSError:
        pytest.skip("this machine cannot create a hard link")
    server = _serve(project)
    try:
        host, port = server.server_address
        status, body = _get(host, port, "/api/file?path=alias.txt", server.page_token)
        plain_status, plain_body = _get(host, port, "/api/file?path=note.txt", server.page_token)
    finally:
        server.shutdown()
        server.server_close()
    assert status == 400
    assert _CANARY not in body
    assert plain_status == 200
    assert plain_body == b"a plain note\n"
    assert secret.read_bytes() == _CANARY


def test_a_workspace_file_through_a_linked_folder_is_not_shown(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_bytes(_CANARY)
    _link_dir(project / "notes", outside)
    server = _serve(project)
    try:
        host, port = server.server_address
        status, body = _get(host, port, "/api/file?path=notes/secret.txt", server.page_token)
    finally:
        server.shutdown()
        server.server_close()
    assert status == 400
    assert b"outside the project" in body
    assert _CANARY not in body
    assert secret.read_bytes() == _CANARY


def test_a_workspace_file_through_an_inside_link_is_not_shown(project, tmp_path):
    kept = project / "kept"
    kept.mkdir()
    secret = kept / "secret.txt"
    secret.write_bytes(_CANARY)
    _link_dir(project / "notes", kept)
    server = _serve(project)
    try:
        host, port = server.server_address
        status, body = _get(host, port, "/api/file?path=notes/secret.txt", server.page_token)
        direct_status, direct_body = _get(host, port, "/api/file?path=kept/secret.txt", server.page_token)
    finally:
        server.shutdown()
        server.server_close()
    assert status == 400
    assert b"is a link" in body
    assert _CANARY not in body
    assert direct_status == 200
    assert direct_body == _CANARY
