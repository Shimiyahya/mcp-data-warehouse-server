"""The tool functions against the real (test) warehouse."""

from __future__ import annotations

import pytest

from mcp_warehouse.config import ALLOWED_TABLES, MAX_ROWS
from mcp_warehouse.db import get_warehouse
from mcp_warehouse.sql_guard import SQLGuardError


def test_list_tables_matches_allow_list() -> None:
    wh = get_warehouse()
    assert {t["table"] for t in wh.list_tables()} == set(ALLOWED_TABLES)


def test_describe_table_returns_columns_and_sample() -> None:
    wh = get_warehouse()
    info = wh.describe_table("invoice")
    names = {c["name"] for c in info["columns"]}
    assert {"invoice_id", "counterparty_id", "amount_minor", "status"} <= names
    assert info["sample"]["rows"]
    assert info["description"]


def test_describe_table_rejects_unknown() -> None:
    wh = get_warehouse()
    with pytest.raises(SQLGuardError):
        wh.describe_table("not_a_table")


def test_query_returns_rows() -> None:
    wh = get_warehouse()
    result = wh.run_query("SELECT COUNT(*) AS n FROM invoice")
    assert result.columns == ["n"]
    assert result.rows[0][0] == 3000
    assert not result.truncated


def test_query_caps_large_result() -> None:
    wh = get_warehouse()
    result = wh.run_query("SELECT * FROM cash_balance_daily")
    assert result.row_count == MAX_ROWS
    assert result.truncated


def test_query_rejects_write() -> None:
    wh = get_warehouse()
    with pytest.raises(SQLGuardError):
        wh.run_query("DELETE FROM invoice")


def test_query_can_join_and_aggregate() -> None:
    wh = get_warehouse()
    result = wh.run_query(
        "SELECT c.sector, COUNT(*) AS n "
        "FROM invoice i JOIN counterparty c USING (counterparty_id) "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    assert result.columns == ["sector", "n"]
    assert result.row_count >= 1
