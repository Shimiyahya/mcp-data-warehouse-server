"""MCP Data Warehouse Server (FastMCP, stdio).

Exposes the synthetic treasury warehouse as MCP tools, resources, and a prompt.
All database access goes through the read-only, allow-listed, audited path in
:mod:`db`. Diagnostics go to stderr only; stdout is the JSON-RPC channel.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .audit import audit
from .config import ALLOWED_TABLES, DATA_DICTIONARY
from .db import QueryError, QueryResult, get_warehouse
from .sql_guard import SQLGuardError

mcp = FastMCP("MCP Data Warehouse")


# ── helpers ───────────────────────────────────────────────────────────────
def _md_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _markdown_table(columns: list[str], rows: list[list[Any]], max_rows: int = 50) -> str:
    if not columns:
        return "_(no columns)_"
    if not rows:
        return "_(0 rows)_"
    shown = rows[:max_rows]
    header = "| " + " | ".join(str(c) for c in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = "\n".join("| " + " | ".join(_md_cell(v) for v in r) + " |" for r in shown)
    out = "\n".join([header, sep, body])
    if len(rows) > max_rows:
        out += (
            f"\n\n_…and {len(rows) - max_rows} more rows (structured `rows` holds the full set)._"
        )
    return out


# ── tools ─────────────────────────────────────────────────────────────────
@mcp.tool()
def list_tables() -> dict[str, Any]:
    """List the queryable warehouse tables with row-count estimates and descriptions.

    Start here, then call describe_table for the tables you need before writing SQL.
    """
    wh = get_warehouse()
    tables = wh.list_tables()
    audit(tool="list_tables", decision="allowed", status="ok", count=len(tables))
    return {"tables": tables}


@mcp.tool()
def describe_table(name: str) -> dict[str, Any]:
    """Return columns, types, the data dictionary, and a small sample for one table."""
    wh = get_warehouse()
    try:
        info = wh.describe_table(name)
    except SQLGuardError as exc:
        audit(
            tool="describe_table",
            args={"name": name},
            decision="denied",
            reason=exc.reason,
            status="error",
        )
        return {"error": str(exc), "reason": exc.reason, "allowed_tables": sorted(ALLOWED_TABLES)}
    audit(tool="describe_table", args={"name": name}, decision="allowed", status="ok")
    return info


@mcp.tool()
def query(sql: str, limit: int | None = None) -> dict[str, Any]:
    """Run a READ-ONLY SQL query against the warehouse and return the rows.

    Only a single SELECT/WITH statement over the allow-listed tables is permitted;
    INSERT/UPDATE/DDL, multiple statements, and file/network functions are rejected.
    A LIMIT is enforced and the result is capped, so prefer aggregation in SQL over
    pulling raw rows. Amounts are integer minor units; convert currencies via fx_rate.
    """
    wh = get_warehouse()
    try:
        result: QueryResult = wh.run_query(sql, requested_limit=limit)
    except SQLGuardError as exc:
        audit(tool="query", args={"sql": sql}, decision="denied", reason=exc.reason, status="error")
        return {"error": str(exc), "reason": exc.reason}
    except QueryError as exc:
        audit(
            tool="query",
            args={"sql": sql},
            decision="allowed",
            status="error",
            reason=exc.reason,
        )
        return {"error": str(exc), "reason": exc.reason}

    audit(
        tool="query",
        args={"sql": sql},
        effective_sql=result.effective_sql,
        decision="allowed",
        status="ok",
        row_count=result.row_count,
        truncated=result.truncated,
        duration_ms=result.duration_ms,
    )
    return {
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "note": result.note,
        "duration_ms": result.duration_ms,
        "markdown": _markdown_table(result.columns, result.rows),
    }


# ── resources ─────────────────────────────────────────────────────────────
@mcp.resource("schema://catalog")
def catalog_resource() -> str:
    """The full table catalog + human data dictionary, as JSON."""
    wh = get_warehouse()
    catalog = {"tables": wh.list_tables(), "data_dictionary": DATA_DICTIONARY}
    return json.dumps(catalog, indent=2, default=str)


@mcp.resource("table://{name}")
def table_resource(name: str) -> str:
    """Columns, types, dictionary, and a sample for a single table, as JSON."""
    wh = get_warehouse()
    try:
        return json.dumps(wh.describe_table(name), indent=2, default=str)
    except SQLGuardError as exc:
        return json.dumps({"error": str(exc), "reason": exc.reason})


# ── prompt ────────────────────────────────────────────────────────────────
@mcp.prompt()
def analyze_cashflow(question: str = "") -> str:
    """A starter prompt that primes the model to explore the schema and answer a
    cash-flow / treasury question against this warehouse."""
    base = (
        "You are a financial analyst with read-only access to a treasury & payments "
        "data warehouse via MCP tools (list_tables, describe_table, query). "
        "Workflow: (1) call list_tables; (2) describe_table for the tables you need; "
        "(3) write read-only SELECT/WITH queries. The server enforces SELECT-only and "
        "caps rows, so push aggregation into SQL. Money is stored as integer minor units; "
        "convert non-GBP amounts via the fx_rate table (join on rate_date + currency) to "
        "report in GBP. Show the SQL you run and explain your reasoning."
    )
    q = question.strip()
    if q:
        return f"{base}\n\nQuestion: {q}"
    return f"{base}\n\nAsk the user what they'd like to know, then solve it step by step."


def main() -> None:
    """Console-script entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
