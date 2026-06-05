"""The seed is deterministic (seed=42): exact row counts + referential integrity."""

from __future__ import annotations

import os

import duckdb
import pytest

# Exact counts produced by scripts/seed.py with seed=42 (determinism contract).
EXPECTED_COUNTS = {
    "counterparty": 120,
    "bank_account": 12,
    "gl_account": 40,
    "fx_rate": 1462,
    "invoice": 3000,
    "payment": 4500,
    "payment_allocation": 1491,
    "cash_balance_daily": 8041,
}


def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(os.environ["MCP_WAREHOUSE_DB"], read_only=True)


@pytest.mark.parametrize("table,count", EXPECTED_COUNTS.items())
def test_row_counts_are_deterministic(table: str, count: int) -> None:
    con = _con()
    assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count


def test_no_orphan_foreign_keys() -> None:
    con = _con()
    checks = [
        "SELECT COUNT(*) FROM payment_allocation "
        "WHERE payment_id NOT IN (SELECT payment_id FROM payment)",
        "SELECT COUNT(*) FROM payment_allocation "
        "WHERE invoice_id NOT IN (SELECT invoice_id FROM invoice)",
        "SELECT COUNT(*) FROM invoice "
        "WHERE counterparty_id NOT IN (SELECT counterparty_id FROM counterparty)",
        "SELECT COUNT(*) FROM payment "
        "WHERE account_id NOT IN (SELECT account_id FROM bank_account)",
    ]
    for sql in checks:
        assert con.execute(sql).fetchone()[0] == 0


def test_money_is_non_negative_bigint() -> None:
    con = _con()
    assert con.execute("SELECT COUNT(*) FROM invoice WHERE amount_minor < 0").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM payment WHERE amount_minor < 0").fetchone()[0] == 0
    assert (
        con.execute(
            "SELECT COUNT(*) FROM cash_balance_daily WHERE closing_balance_minor < 0"
        ).fetchone()[0]
        == 0
    )
