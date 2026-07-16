"""The most important file: the SQL guard must reject everything that isn't a
single, read-only, allow-listed SELECT, and accept legitimate analytics."""

from __future__ import annotations

import pytest

from mcp_warehouse.sql_guard import SQLGuardError, validate_and_prepare

REJECTED = [
    ("DROP TABLE invoice", "non_select"),
    ("DELETE FROM invoice", "non_select"),
    ("UPDATE invoice SET amount_minor = 0", "non_select"),
    ("INSERT INTO invoice VALUES (1)", "non_select"),
    ("CREATE TABLE x (a INTEGER)", "non_select"),
    ("ALTER TABLE invoice ADD COLUMN x INT", "non_select"),
    ("SELECT 1; DROP TABLE invoice", "multiple_statements"),
    ("SELECT 1; SELECT 2", "multiple_statements"),
    ("COPY invoice TO 'out.csv'", "non_select"),
    ("ATTACH 'evil.db'", "non_select"),
    ("PRAGMA database_list", "non_select"),
    ("SELECT * FROM read_csv('/etc/passwd')", "function_blocked"),
    ("SELECT * FROM read_parquet('s3://x/y')", "function_blocked"),
    ("SELECT * FROM secret_table", "table_not_allowed"),
    ("SELECT * FROM information_schema.columns", "table_not_allowed"),
    ("SELECT * FROM duckdb_tables()", "function_blocked"),
    ("", "empty"),
]

ACCEPTED = [
    "SELECT 1",
    "SELECT * FROM invoice",
    "SELECT COUNT(*) FROM payment WHERE status = 'settled'",
    "WITH paid AS (SELECT counterparty_id FROM invoice WHERE status='paid') SELECT * FROM paid",
    "SELECT c.legal_name, SUM(i.amount_minor) FROM invoice i "
    "JOIN counterparty c USING (counterparty_id) GROUP BY 1 ORDER BY 2 DESC",
    "SELECT * FROM payment UNION ALL SELECT * FROM payment",
]


@pytest.mark.parametrize("sql,reason", REJECTED)
def test_rejects_unsafe_sql(sql: str, reason: str) -> None:
    with pytest.raises(SQLGuardError) as exc:
        validate_and_prepare(sql)
    assert exc.value.reason == reason


@pytest.mark.parametrize("sql", ACCEPTED)
def test_accepts_read_only_queries(sql: str) -> None:
    prepared = validate_and_prepare(sql)
    assert prepared.sql.strip()


def test_limit_is_injected_when_absent() -> None:
    prepared = validate_and_prepare("SELECT * FROM invoice")
    assert prepared.capped
    assert "limit" in prepared.sql.lower()


def test_oversized_limit_is_clamped() -> None:
    prepared = validate_and_prepare("SELECT * FROM invoice LIMIT 999999")
    assert prepared.capped


def test_small_limit_is_respected() -> None:
    prepared = validate_and_prepare("SELECT * FROM invoice LIMIT 5")
    assert not prepared.capped
    assert prepared.row_cap == 5
