"""An MCP resource reports a project error as that error."""

import asyncio

import pytest

from aicad.errors import GeometryError
from aicad.server import build_server


def test_a_resource_read_returns_the_project_error(tmp_path, monkeypatch):
    from mcp.server.mcpserver.exceptions import ResourceError, UnexpectedResourceError

    def boom(self, name, arguments=None, source="agent"):
        raise GeometryError("parts could not be listed")

    monkeypatch.setattr("aicad.workspace.Workspace.execute", boom)
    server = build_server(tmp_path)
    with pytest.raises(ResourceError, match="parts could not be listed") as raised:
        asyncio.run(server.read_resource("aicad://parts"))
    assert type(raised.value) is ResourceError
    assert not isinstance(raised.value, UnexpectedResourceError)
    assert "Traceback" not in str(raised.value)


def test_a_resource_read_returns_an_unexpected_project_failure(tmp_path, monkeypatch):
    from mcp.server.mcpserver.exceptions import ResourceError, UnexpectedResourceError

    def boom(self, name, arguments=None, source="agent"):
        raise RuntimeError("the part list failed")

    monkeypatch.setattr("aicad.workspace.Workspace.execute", boom)
    server = build_server(tmp_path)
    with pytest.raises(ResourceError, match="RuntimeError: the part list failed") as raised:
        asyncio.run(server.read_resource("aicad://assemblies/missing"))
    assert type(raised.value) is ResourceError
    assert not isinstance(raised.value, UnexpectedResourceError)
