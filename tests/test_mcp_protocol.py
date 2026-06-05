"""Exercises the real MCP wiring over stdio: initialize, list primitives, call a tool.
Proves the server speaks the protocol, not just that the Python functions work."""

from __future__ import annotations

import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_stdio_roundtrip() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_warehouse.server"],
        env=dict(os.environ),  # carries MCP_WAREHOUSE_DB → server uses the test DB
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = {t.name for t in (await session.list_tools()).tools}
        assert {"list_tables", "describe_table", "query"} <= tools

        resources = await session.list_resource_templates()
        templates = {t.uriTemplate for t in resources.resourceTemplates}
        assert any("table://" in t for t in templates)

        result = await session.call_tool("query", {"sql": "SELECT COUNT(*) AS n FROM invoice"})
        assert result.isError is False
