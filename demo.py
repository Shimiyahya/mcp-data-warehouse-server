"""End-to-end demo: drive this MCP server with Claude over stdio.

Spawns the server as a stdio subprocess, discovers its tools, and runs a manual
agentic loop (so the reasoning trace is visible) until Claude answers a
multi-step treasury question that requires several joins and an FX conversion.

Run:  uv run python demo.py        (needs ANTHROPIC_API_KEY, see .env.example)

The model plans the SQL itself; the server only ever runs guarded, read-only
queries. Everything below uses the official Anthropic + MCP Python SDKs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import anthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = "claude-opus-4-8"

SYSTEM = (
    "You are a treasury analyst with read-only access to a financial data warehouse "
    "via MCP tools (list_tables, describe_table, query). Workflow: call list_tables, "
    "then describe_table for the tables you need, then write read-only SELECT/WITH "
    "queries. The server enforces SELECT-only and caps rows, so push aggregation into "
    "SQL. Money is stored as integer minor units; convert non-GBP amounts via the "
    "fx_rate table (join on rate_date + currency) to report in GBP. Show the SQL you run."
)

QUESTION = (
    "Which 3 counterparties drove the largest net cash outflow in GBP during Q3 2024 "
    "(convert EUR/USD payments at the value-date FX rate to GBP), and for each, what "
    "fraction of their payable invoices is still open past its due date?"
)


def _tool_result_text(result) -> str:
    """Flatten an MCP CallToolResult into a string for a tool_result block."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, default=str)
    parts = [getattr(b, "text", "") for b in result.content if getattr(b, "text", "")]
    return "\n".join(parts) if parts else "(no content)"


async def main() -> None:
    load_dotenv()
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")

    # Launch the server with this same interpreter so no PATH/uv lookup is needed.
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_warehouse.server"])
    client = anthropic.AsyncAnthropic()

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
            for t in listed.tools
        ]
        print(
            f"Connected to MCP server over stdio with {len(tools)} tools: "
            f"{', '.join(t['name'] for t in tools)}\n"
        )
        print(f"Q: {QUESTION}\n" + "─" * 70)

        messages: list[dict] = [{"role": "user", "content": QUESTION}]
        tool_calls = tokens_in = tokens_out = cache_read = 0
        start = time.perf_counter()

        for _turn in range(16):  # safety cap on agentic turns
            resp = await client.messages.create(
                model=MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive", "display": "summarized"},
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
            )
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens
            cache_read += getattr(resp.usage, "cache_read_input_tokens", 0) or 0

            for block in resp.content:
                if block.type == "thinking" and getattr(block, "thinking", "").strip():
                    print(f"\n  [reasoning] {block.thinking.strip()[:280]}")
                elif block.type == "text" and block.text.strip():
                    print(f"\n=== ANSWER ===\n{block.text.strip()}")

            if resp.stop_reason != "tool_use":
                break

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                tool_calls += 1
                print(f"\n  [Claude → {block.name}] {json.dumps(block.input)[:200]}")
                result = await session.call_tool(block.name, block.input)
                text = _tool_result_text(result)
                preview = text[:160] + ("…" if len(text) > 160 else "")
                print(f"  [server → Claude] {preview}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": text})
            messages.append({"role": "user", "content": results})

        elapsed = time.perf_counter() - start
        print("\n" + "─" * 70)
        print(
            f"tool calls: {tool_calls}  ·  tokens in/out: {tokens_in}/{tokens_out}  ·  "
            f"cache read: {cache_read}  ·  {elapsed:.1f}s"
        )


if __name__ == "__main__":
    asyncio.run(main())
