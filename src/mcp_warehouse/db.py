"""Read-only DuckDB access layer.

Owns the single hardened, read-only connection and all execution guard-rails:
SQL validation (via :mod:`sql_guard`), wall-clock timeout, and row/byte caps.
Schema discovery is filtered through the same allow-list as queries.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from .config import (
    ALLOWED_TABLES,
    DATA_DICTIONARY,
    MAX_BYTES,
    QUERY_TIMEOUT_S,
    SAMPLE_ROWS,
    db_path,
)
from .sql_guard import SQLGuardError, validate_and_prepare

# Security-critical settings applied at connect time (reliable), the rest via SET.
_CONNECT_CONFIG = {
    "enable_external_access": "false",
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
}
_POST_CONNECT = [
    "SET allow_community_extensions=false",
    "SET memory_limit='512MB'",
    "SET threads=2",
]


class QueryError(Exception):
    """Execution-time failure (timeout, db error). ``reason`` is a short code."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    duration_ms: float
    effective_sql: str
    note: str = ""


def _jsonable(value: Any) -> Any:
    """Coerce DuckDB cell values into JSON-friendly Python."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (_dt.date, _dt.datetime, _dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return str(value)


def _enforce_byte_cap(rows: list[list[Any]]) -> tuple[list[list[Any]], bool]:
    """Drop trailing rows until the serialized payload fits under MAX_BYTES."""
    if not rows:
        return rows, False
    if len(json.dumps(rows, default=str).encode("utf-8")) <= MAX_BYTES:
        return rows, False
    kept = rows
    while kept and len(json.dumps(kept, default=str).encode("utf-8")) > MAX_BYTES:
        cut = max(1, len(kept) // 10)
        kept = kept[:-cut]
    return kept, True


class Warehouse:
    """A single, hardened, read-only connection to the warehouse DuckDB file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else db_path()
        if not self.path.exists():
            raise FileNotFoundError(
                f"Warehouse database not found at {self.path}. "
                "Build it first:  uv run python scripts/seed.py"
            )
        self._con = duckdb.connect(str(self.path), read_only=True, config=_CONNECT_CONFIG)
        for stmt in _POST_CONNECT:
            try:
                self._con.execute(stmt)
            except duckdb.Error as exc:  # setting may not exist on this build
                print(f"[mcp-warehouse] hardening skipped ({stmt!r}): {exc}", file=sys.stderr)
        # Lock LAST so an injected SET can't re-enable anything above.
        try:
            self._con.execute("SET lock_configuration=true")
        except duckdb.Error as exc:
            print(f"[mcp-warehouse] could not lock configuration: {exc}", file=sys.stderr)

        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="duckdb")

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        try:
            self._con.close()
        except duckdb.Error:
            pass

    # ── schema discovery (allow-list filtered) ────────────────────────────
    def list_tables(self) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT table_name, estimated_size FROM duckdb_tables() WHERE schema_name = 'main'"
        ).fetchall()
        out = [
            {
                "table": name,
                "estimated_rows": int(est) if est is not None else None,
                "description": DATA_DICTIONARY.get(name, {}).get("description"),
            }
            for name, est in rows
            if name in ALLOWED_TABLES
        ]
        out.sort(key=lambda d: d["table"])
        return out

    def describe_table(self, name: str) -> dict[str, Any]:
        key = (name or "").strip().lower()
        if key not in ALLOWED_TABLES:
            raise SQLGuardError(f"Table not allowed: {name}", "table_not_allowed")
        cols = self._con.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'main' AND lower(table_name) = ? ORDER BY ordinal_position",
            [key],
        ).fetchall()
        dd = DATA_DICTIONARY.get(key, {})
        col_docs = dd.get("columns", {})
        columns = [
            {
                "name": c[0],
                "type": c[1],
                "nullable": c[2] == "YES",
                "description": col_docs.get(c[0]),
            }
            for c in cols
        ]
        return {
            "table": key,
            "description": dd.get("description"),
            "columns": columns,
            "sample": self._sample(key),
        }

    def _sample(self, table: str) -> dict[str, Any]:
        # `table` is allow-listed; quote it as an identifier regardless.
        cur = self._con.execute(f'SELECT * FROM "{table}" LIMIT {SAMPLE_ROWS}')
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [[_jsonable(v) for v in row] for row in cur.fetchall()]
        return {"columns": columns, "rows": rows}

    # ── guarded query path ────────────────────────────────────────────────
    def run_query(self, sql: str, requested_limit: int | None = None) -> QueryResult:
        prepared = validate_and_prepare(sql, requested_limit=requested_limit)
        start = time.perf_counter()
        columns, raw = self._execute_with_timeout(prepared.sql)
        duration_ms = (time.perf_counter() - start) * 1000

        truncated = False
        note = ""
        if prepared.capped and len(raw) > prepared.row_cap:
            truncated = True
            raw = raw[: prepared.row_cap]
            note = f"Showing the first {prepared.row_cap} rows (row limit applied)."

        rows = [[_jsonable(v) for v in row] for row in raw]
        rows, byte_truncated = _enforce_byte_cap(rows)
        if byte_truncated:
            truncated = True
            note = (
                f"Result truncated to fit a {MAX_BYTES // 1024} KB payload cap. "
                "Refine with WHERE / aggregation."
            )

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            duration_ms=round(duration_ms, 2),
            effective_sql=prepared.sql,
            note=note,
        )

    def _execute(self, sql: str) -> tuple[list[str], list[tuple]]:
        cur = self._con.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        return columns, cur.fetchall()

    def _execute_with_timeout(self, sql: str) -> tuple[list[str], list[tuple]]:
        with self._lock:
            future = self._executor.submit(self._execute, sql)
            try:
                return future.result(timeout=QUERY_TIMEOUT_S)
            except FutureTimeout as exc:
                try:
                    self._con.interrupt()
                except duckdb.Error:
                    pass
                # Abandon the stuck worker; spin up a fresh one for later calls.
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="duckdb")
                raise QueryError(
                    f"Query exceeded the {QUERY_TIMEOUT_S:g}s time limit.", "timeout"
                ) from exc
            except duckdb.Error as exc:
                raise QueryError(str(exc), "db_error") from exc


# ── lazy singleton ────────────────────────────────────────────────────────
_WAREHOUSE: Warehouse | None = None
_singleton_lock = threading.Lock()


def get_warehouse() -> Warehouse:
    global _WAREHOUSE
    if _WAREHOUSE is None:
        with _singleton_lock:
            if _WAREHOUSE is None:
                _WAREHOUSE = Warehouse()
    return _WAREHOUSE
