"""Central configuration: the table allow-list, guard-rail limits, and the
human-written data dictionary (semantics matter more than raw types for LLM
accuracy)."""

from __future__ import annotations

import os
from pathlib import Path

# ── Allow-list ────────────────────────────────────────────────────────────
# Only these tables are visible/queryable. Default-deny everything else
# (system catalogs, new tables, file/network table functions).
ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "counterparty",
        "bank_account",
        "gl_account",
        "fx_rate",
        "invoice",
        "payment",
        "payment_allocation",
        "cash_balance_daily",
    }
)

# ── Guard rails ───────────────────────────────────────────────────────────
MAX_ROWS = 1000  # hard row cap (a LIMIT is injected/clamped to this)
MAX_BYTES = 256 * 1024  # hard cap on the serialized result payload
QUERY_TIMEOUT_S = 10.0  # wall-clock timeout per query
SAMPLE_ROWS = 5  # rows returned by describe_table previews

# Functions that read the filesystem / network, denied even though the
# connection is already read-only, as defense in depth.
BLOCKED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "parquet_scan",
        "read_json",
        "read_json_auto",
        "read_text",
        "read_blob",
        "glob",
        "csv_scan",
        "icu_collate",
        "sniff_csv",
    }
)


def db_path() -> Path:
    """Path to the DuckDB file. Override with ``MCP_WAREHOUSE_DB``."""
    env = os.environ.get("MCP_WAREHOUSE_DB")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / "warehouse.duckdb"


def audit_log_path() -> Path:
    """Path to the JSONL audit log. Override with ``MCP_WAREHOUSE_AUDIT_LOG``."""
    env = os.environ.get("MCP_WAREHOUSE_AUDIT_LOG")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / "logs" / "audit.jsonl"


# ── Data dictionary ───────────────────────────────────────────────────────
# Surfaced via describe_table and the schema:// resources so the model gets
# *meaning*, not just column types.
DATA_DICTIONARY: dict[str, dict] = {
    "counterparty": {
        "description": "Customers, vendors, and intra-group entities Northwind Pay transacts with.",
        "columns": {
            "counterparty_id": "Surrogate primary key.",
            "legal_name": "Registered legal name.",
            "country_code": "ISO-3166 alpha-2 country.",
            "sector": "One of: energy, retail, saas, logistics, financial.",
            "is_internal": "True for intra-group entities (eliminate for external-facing figures).",
            "onboarded_at": "Date the counterparty was first onboarded.",
        },
    },
    "bank_account": {
        "description": "The company's own bank accounts, one per currency/entity.",
        "columns": {
            "account_id": "Surrogate primary key.",
            "entity_name": "Owning legal entity.",
            "currency": "ISO-4217 currency of the account (GBP, EUR, USD).",
            "iban": "Synthetic IBAN (not a real account).",
            "opened_at": "Account open date.",
            "is_active": "Whether the account is currently active.",
        },
    },
    "gl_account": {
        "description": "Chart of accounts (general-ledger codes).",
        "columns": {
            "gl_code": "Primary key, e.g. '4000'.",
            "name": "Account name.",
            "type": "One of: asset, liability, revenue, expense.",
        },
    },
    "fx_rate": {
        "description": "Daily FX rates to convert amounts into a common reporting currency. "
        "Join on (rate_date, base_ccy, quote_ccy). Use quote_ccy='GBP' to report in GBP.",
        "columns": {
            "rate_date": "Date the rate applies to.",
            "base_ccy": "Currency being converted from (EUR, USD).",
            "quote_ccy": "Currency being converted to (GBP).",
            "rate": "Multiply a base_ccy amount by this to get quote_ccy. DECIMAL(18,8).",
        },
    },
    "invoice": {
        "description": "Accounts-receivable and accounts-payable invoices. Grain for AR/AP aging.",
        "columns": {
            "invoice_id": "Surrogate primary key.",
            "counterparty_id": "FK -> counterparty.",
            "direction": "'receivable' (they owe us) or 'payable' (we owe them).",
            "issue_date": "Invoice issue date.",
            "due_date": "Payment due date (used for past-due / aging).",
            "currency": "ISO-4217 currency of the invoice.",
            "amount_minor": "Invoice amount in integer minor units (e.g. pennies).",
            "status": "'open', 'paid', 'partial', or 'void'.",
        },
    },
    "payment": {
        "description": "Inbound and outbound payments. Some fail or return, "
        "which settlement-rate questions depend on.",
        "columns": {
            "payment_id": "Surrogate primary key.",
            "account_id": "FK -> bank_account the payment moved through.",
            "counterparty_id": "FK -> counterparty.",
            "value_date": "Settlement/value date.",
            "currency": "ISO-4217 currency of the payment.",
            "amount_minor": "Payment amount in integer minor units.",
            "direction": "'inbound' (money in) or 'outbound' (money out).",
            "method": "'sepa', 'swift', 'faster_payments', 'card', or 'internal'.",
            "status": "'settled', 'pending', 'failed', or 'returned'.",
        },
    },
    "payment_allocation": {
        "description": "Bridge table: which payment cleared which invoice, and how much. "
        "Many-to-many: one payment can clear several invoices and an invoice "
        "can be paid in installments.",
        "columns": {
            "payment_id": "FK -> payment.",
            "invoice_id": "FK -> invoice.",
            "allocated_minor": "Amount of the payment applied to that invoice (minor units).",
        },
    },
    "cash_balance_daily": {
        "description": "Daily closing balance per bank account. Enables "
        "liquidity / runway / rolling-window questions.",
        "columns": {
            "account_id": "FK -> bank_account.",
            "balance_date": "The day of the closing balance.",
            "closing_balance_minor": "End-of-day balance in integer minor units.",
        },
    },
}
