"""Read-only SQL safety guard.

Every query the LLM proposes is parsed to an AST with ``sqlglot`` and accepted
only if it is a *single* top-level ``SELECT``/``WITH``/set-operation that touches
*only* allow-listed tables and no file/network/catalog functions. A ``LIMIT`` is
then injected or clamped. Treat the model as an untrusted, prompt-injectable SQL
author — this is defense in depth on top of the physically read-only connection.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from .config import ALLOWED_TABLES, BLOCKED_FUNCTIONS, MAX_ROWS

# Read-only query shapes we permit at the top level.
_ALLOWED_TOP = (exp.Select, exp.Union, exp.Intersect, exp.Except)


class SQLGuardError(ValueError):
    """Raised when a query violates the safety policy. ``reason`` is a short code."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class PreparedQuery:
    sql: str  # the effective, re-serialized SQL actually executed
    row_cap: int  # max rows to return to the caller
    capped: bool  # True if we imposed/clamped the LIMIT (so > row_cap means truncated)


def _limit_value(query: exp.Expression) -> int | None:
    """Return the integer LIMIT already on a query, or None if absent/non-literal."""
    lim = query.args.get("limit")
    if lim is None:
        return None
    expr = lim.args.get("expression")
    if isinstance(expr, exp.Literal):
        try:
            return int(expr.name)
        except (ValueError, TypeError):
            return None
    return None


def _func_name(node: exp.Expression) -> str:
    """Canonical lowercase function name (handles both known funcs and Anonymous)."""
    if isinstance(node, exp.Anonymous):
        return str(node.this or "").lower()
    try:
        return node.sql_name().lower()
    except Exception:  # pragma: no cover - defensive
        return type(node).__name__.lower()


def validate_and_prepare(
    sql: str,
    *,
    allowed_tables: frozenset[str] = ALLOWED_TABLES,
    max_rows: int = MAX_ROWS,
    requested_limit: int | None = None,
) -> PreparedQuery:
    """Validate a read-only query and return the effective SQL with LIMIT enforced.

    Raises :class:`SQLGuardError` (with a ``reason`` code) on any violation.
    """
    if not sql or not sql.strip():
        raise SQLGuardError("Empty query.", "empty")

    # 1) Parse. A real parser — never split on ';' or keyword-match.
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="duckdb") if s is not None]
    except Exception as exc:  # noqa: BLE001 - surface any parse failure as a denial
        raise SQLGuardError(f"Could not parse SQL: {exc}", "parse_error") from exc

    # 2) Exactly one statement (blocks stacked-statement attacks: "... ; DROP ...").
    if not statements:
        raise SQLGuardError("No statement found.", "empty")
    if len(statements) > 1:
        raise SQLGuardError("Only a single statement is allowed.", "multiple_statements")

    stmt = statements[0]

    # Unwrap a parenthesized top-level query, e.g. "(SELECT ...)".
    inner = stmt
    while isinstance(inner, exp.Subquery) and inner.this is not None:
        inner = inner.this

    # 3) Positive allow-list of statement type (default-deny everything else:
    #    INSERT/UPDATE/DELETE/CREATE/DROP/ALTER/ATTACH/COPY/PRAGMA/SET/CALL...).
    if not isinstance(inner, _ALLOWED_TOP):
        raise SQLGuardError("Only read-only SELECT / WITH queries are allowed.", "non_select")

    # CTE names are local aliases, not real tables — don't allow-list-check them.
    cte_names = {c.alias_or_name.lower() for c in inner.find_all(exp.CTE)}

    # 4) Every FROM/JOIN source must be a plain identifier table on the allow-list.
    #    A non-identifier source is a table-valued function such as
    #    read_csv('/etc/passwd') / read_parquet('s3://…') — denied outright (this
    #    is the file/network exfiltration class, and it parses as Table(this=Func)).
    for table in inner.find_all(exp.Table):
        src = table.this
        if not isinstance(src, exp.Identifier):
            raise SQLGuardError(
                f"Table source not allowed: {table.sql(dialect='duckdb')}",
                "function_blocked",
            )
        name = src.name.lower()
        if name in cte_names:
            continue
        if name not in allowed_tables:
            raise SQLGuardError(f"Table not allowed: {name}", "table_not_allowed")

    # 5) Block file/network/catalog functions anywhere they appear (read_csv,
    #    read_parquet, duckdb_*, pg_*), matched by canonical name regardless of args.
    for node in inner.find_all(exp.Func):
        fname = _func_name(node)
        if fname in BLOCKED_FUNCTIONS or fname.startswith(("duckdb_", "pg_")):
            raise SQLGuardError(f"Function not allowed: {fname}", "function_blocked")

    # 6) Enforce / clamp LIMIT.
    cap = max_rows
    if requested_limit is not None:
        try:
            req = int(requested_limit)
            if req > 0:
                cap = min(cap, req)
        except (TypeError, ValueError):
            pass

    existing = _limit_value(inner)
    if existing is None or existing > cap:
        # We impose the cap. Fetch cap+1 so the DB layer can detect truncation.
        prepared = inner.limit(cap + 1)
        return PreparedQuery(sql=prepared.sql(dialect="duckdb"), row_cap=cap, capped=True)

    # User asked for <= cap rows — respect it exactly.
    return PreparedQuery(sql=inner.sql(dialect="duckdb"), row_cap=existing, capped=False)
