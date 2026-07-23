"""Smoke-test a running HTTP-mode server: /health, then a real MCP roundtrip.

Run:  uv run python scripts/smoke_http.py [base_url]   (default http://127.0.0.1:8000)

Exits 0 only if the health probe answers, the MCP handshake completes, and a
read-only query returns rows. This is the same check the k8s definition of
done uses after `helm install` + port-forward.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main(base: str) -> None:
    health = httpx.get(f"{base}/health", timeout=5)
    health.raise_for_status()
    print(f"health: {health.json()}")

    async with (
        streamablehttp_client(f"{base}/mcp") as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = sorted(t.name for t in (await session.list_tools()).tools)
        print(f"tools: {tools}")

        result = await session.call_tool(
            "query",
            {"sql": "SELECT status, COUNT(*) AS n FROM payment GROUP BY status ORDER BY n DESC"},
        )
        if result.isError:
            raise SystemExit(f"query failed: {result.content}")
        print(f"query result:\n{result.content[0].text}")
        print("SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
