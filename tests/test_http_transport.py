"""Exercises the streamable-HTTP transport end to end: boot the server as a
subprocess in HTTP mode, hit /health (what k8s probes will do), then run a
real MCP initialize + tool call over HTTP."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_server() -> Iterator[str]:
    port = _free_port()
    env = dict(os.environ)  # carries MCP_WAREHOUSE_DB → server uses the test DB
    env.update({"MCP_TRANSPORT": "streamable-http", "MCP_HOST": "127.0.0.1", "MCP_PORT": str(port)})
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_warehouse.server"],
        env=env,
        stdout=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if proc.poll() is not None:
                pytest.fail(f"server exited early with code {proc.returncode}")
            if time.monotonic() > deadline:
                pytest.fail("server did not become healthy within 30s")
            time.sleep(0.2)
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_health_endpoint(http_server: str) -> None:
    resp = httpx.get(f"{http_server}/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_http_roundtrip(http_server: str) -> None:
    async with (
        streamablehttp_client(f"{http_server}/mcp") as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        tools = {t.name for t in (await session.list_tools()).tools}
        assert {"list_tables", "describe_table", "query"} <= tools

        result = await session.call_tool("query", {"sql": "SELECT COUNT(*) AS n FROM invoice"})
        assert result.isError is False
