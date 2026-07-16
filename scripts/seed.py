"""Deterministically generate the synthetic treasury & payments warehouse.

Run:  uv run python scripts/seed.py   (writes ./warehouse.duckdb)

Everything is seeded from a fixed RNG (seed=42), so the database is byte-stable
across machines, which is what makes the demo repeatable and the tests reliable.
All data is fictional. Money is stored as integer minor units (pennies/cents).
"""

from __future__ import annotations

import datetime as dt
import os
import random
from pathlib import Path

import duckdb

SEED = 42
START = dt.date(2023, 1, 1)
END = dt.date(2024, 12, 31)
DAYS = (END - START).days + 1

N_COUNTERPARTIES = 120
N_INTERNAL = 6
N_ACCOUNTS = 12
N_INVOICES = 3000
N_PAYMENTS = 4500

SECTORS = ["energy", "retail", "saas", "logistics", "financial"]
COUNTRIES = ["GB", "FR", "DE", "NL", "IE", "ES", "US"]
CCYS = ["GBP", "EUR", "USD"]
NAME_A = [
    "North",
    "Brcompton",
    "Acme",
    "Vega",
    "Helio",
    "Ardent",
    "Crest",
    "Lumen",
    "Orbit",
    "Pioneer",
    "Sterling",
    "Maple",
    "Quartz",
    "Delta",
    "Harbor",
    "Cobalt",
    "Vertex",
    "Onyx",
    "Aspen",
    "Beacon",
    "Granite",
    "Halcyon",
]
NAME_B = [
    "Energy",
    "Retail",
    "Logistics",
    "Systems",
    "Capital",
    "Trading",
    "Labs",
    "Group",
    "Networks",
    "Holdings",
    "Partners",
    "Industries",
    "Solutions",
    "Markets",
]
SUFFIX = ["Ltd", "PLC", "GmbH", "SAS", "BV", "Inc"]
METHODS = ["sepa", "swift", "faster_payments", "card", "internal"]


def db_path() -> Path:
    return Path(os.environ.get("MCP_WAREHOUSE_DB", "warehouse.duckdb")).expanduser()


def rand_date(rng: random.Random, start: dt.date = START, end: dt.date = END) -> dt.date:
    return start + dt.timedelta(days=rng.randint(0, (end - start).days))


def weighted(rng: random.Random, choices: list[tuple[str, float]]) -> str:
    population, weights = zip(*choices, strict=True)
    return rng.choices(population, weights=weights, k=1)[0]


SCHEMA = """
CREATE TABLE counterparty (
    counterparty_id INTEGER PRIMARY KEY,
    legal_name      VARCHAR NOT NULL,
    country_code    VARCHAR NOT NULL,
    sector          VARCHAR NOT NULL,
    is_internal     BOOLEAN NOT NULL,
    onboarded_at    DATE    NOT NULL
);
CREATE TABLE bank_account (
    account_id  INTEGER PRIMARY KEY,
    entity_name VARCHAR NOT NULL,
    currency    VARCHAR NOT NULL,
    iban        VARCHAR NOT NULL,
    opened_at   DATE    NOT NULL,
    is_active   BOOLEAN NOT NULL
);
CREATE TABLE gl_account (
    gl_code VARCHAR PRIMARY KEY,
    name    VARCHAR NOT NULL,
    type    VARCHAR NOT NULL
);
CREATE TABLE fx_rate (
    rate_date DATE          NOT NULL,
    base_ccy  VARCHAR        NOT NULL,
    quote_ccy VARCHAR        NOT NULL,
    rate      DECIMAL(18, 8) NOT NULL,
    PRIMARY KEY (rate_date, base_ccy, quote_ccy)
);
CREATE TABLE invoice (
    invoice_id      INTEGER PRIMARY KEY,
    counterparty_id INTEGER NOT NULL REFERENCES counterparty(counterparty_id),
    direction       VARCHAR NOT NULL,
    issue_date      DATE    NOT NULL,
    due_date        DATE    NOT NULL,
    currency        VARCHAR NOT NULL,
    amount_minor    BIGINT  NOT NULL,
    status          VARCHAR NOT NULL
);
CREATE TABLE payment (
    payment_id      INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES bank_account(account_id),
    counterparty_id INTEGER NOT NULL REFERENCES counterparty(counterparty_id),
    value_date      DATE    NOT NULL,
    currency        VARCHAR NOT NULL,
    amount_minor    BIGINT  NOT NULL,
    direction       VARCHAR NOT NULL,
    method          VARCHAR NOT NULL,
    status          VARCHAR NOT NULL
);
CREATE TABLE payment_allocation (
    payment_id      INTEGER NOT NULL REFERENCES payment(payment_id),
    invoice_id      INTEGER NOT NULL REFERENCES invoice(invoice_id),
    allocated_minor BIGINT  NOT NULL,
    PRIMARY KEY (payment_id, invoice_id)
);
CREATE TABLE cash_balance_daily (
    account_id            INTEGER NOT NULL REFERENCES bank_account(account_id),
    balance_date          DATE    NOT NULL,
    closing_balance_minor BIGINT  NOT NULL,
    PRIMARY KEY (account_id, balance_date)
);
"""


def gen_counterparties(rng: random.Random) -> list[tuple]:
    rows, seen = [], set()
    for cid in range(1, N_COUNTERPARTIES + 1):
        while True:
            name = f"{rng.choice(NAME_A)} {rng.choice(NAME_B)} {rng.choice(SUFFIX)}"
            if name not in seen:
                seen.add(name)
                break
        is_internal = cid <= N_INTERNAL
        rows.append(
            (
                cid,
                name,
                "GB" if is_internal else rng.choice(COUNTRIES),
                rng.choice(SECTORS),
                is_internal,
                rand_date(rng, dt.date(2018, 1, 1), dt.date(2022, 12, 31)),
            )
        )
    return rows


def gen_accounts(rng: random.Random) -> list[tuple]:
    plan = ["GBP"] * 6 + ["EUR"] * 4 + ["USD"] * 2
    rows = []
    for aid, ccy in enumerate(plan, start=1):
        rows.append(
            (
                aid,
                "Northwind Pay Ltd" if aid <= 8 else "Northwind Pay Treasury",
                ccy,
                f"GB{rng.randint(10, 99)}NWPB{rng.randint(10**9, 10**10 - 1)}",
                rand_date(rng, dt.date(2019, 1, 1), dt.date(2021, 12, 31)),
                aid != 12,  # one dormant account
            )
        )
    return rows


def gen_gl_accounts() -> list[tuple]:
    rows = []
    buckets = [("asset", 1000), ("liability", 2000), ("revenue", 4000), ("expense", 5000)]
    names = {
        "asset": ["Cash", "Receivables", "Prepayments", "FX Clearing", "Intercompany"],
        "liability": ["Payables", "Accruals", "Deferred Revenue", "Tax Payable", "Loans"],
        "revenue": ["Subscriptions", "Transaction Fees", "Interchange", "Interest Income", "Other"],
        "expense": ["Payroll", "Cloud Infra", "Card Scheme Fees", "Bank Charges", "Marketing"],
    }
    for gtype, base in buckets:
        for i, nm in enumerate(names[gtype] * 2):  # 10 per type → 40 total
            rows.append((str(base + i * 10), f"{nm} {i // 5 + 1}", gtype))
    return rows


def gen_fx(rng: random.Random) -> list[tuple]:
    rows = []
    bases = {"EUR": 0.86, "USD": 0.79}  # base_ccy -> GBP
    rates = dict(bases)
    for d in (START + dt.timedelta(days=i) for i in range(DAYS)):
        for base, _ in bases.items():
            rates[base] = max(0.5, rates[base] + rng.uniform(-0.004, 0.004))
            rows.append((d, base, "GBP", round(rates[base], 8)))
    return rows


def gen_invoices(rng: random.Random) -> list[tuple]:
    rows = []
    status_choices = [("open", 0.4), ("paid", 0.4), ("partial", 0.1), ("void", 0.1)]
    for iid in range(1, N_INVOICES + 1):
        cp = rng.randint(N_INTERNAL + 1, N_COUNTERPARTIES)  # external only
        issue = rand_date(rng)
        due = issue + dt.timedelta(days=rng.choice([30, 45, 60]))
        rows.append(
            (
                iid,
                cp,
                rng.choice(["receivable", "payable"]),
                issue,
                due,
                rng.choice(CCYS),
                rng.randint(50, 500_000) * 100,
                weighted(rng, status_choices),
            )
        )
    return rows


def gen_payments(rng: random.Random, accounts: list[tuple]) -> list[tuple]:
    rows = []
    status_choices = [("settled", 0.8), ("pending", 0.08), ("failed", 0.07), ("returned", 0.05)]
    by_id = {a[0]: a for a in accounts}
    active_ids = [a[0] for a in accounts if a[5]]
    for pid in range(1, N_PAYMENTS + 1):
        aid = rng.choice(active_ids)
        ccy = by_id[aid][2]
        rows.append(
            (
                pid,
                aid,
                rng.randint(N_INTERNAL + 1, N_COUNTERPARTIES),
                rand_date(rng),
                ccy,
                rng.randint(50, 400_000) * 100,
                rng.choice(["inbound", "outbound"]),
                weighted(
                    rng, [(m, w) for m, w in zip(METHODS, [0.35, 0.2, 0.3, 0.1, 0.05], strict=True)]
                ),
                weighted(rng, status_choices),
            )
        )
    return rows


def gen_allocations(invoices: list[tuple], payments: list[tuple]) -> list[tuple]:
    """Pair settled payments with invoices of the same counterparty + compatible
    direction (receivable<->inbound, payable<->outbound). Valid FKs, unique pairs."""
    compat = {"inbound": "receivable", "outbound": "payable"}
    # invoices that should be cleared, keyed by (counterparty, direction)
    inv_by: dict[tuple[int, str], list[tuple]] = {}
    for inv in invoices:
        iid, cp, direction, _issue, _due, _ccy, amount, status = inv
        if status in ("paid", "partial"):
            inv_by.setdefault((cp, direction), []).append((iid, amount, status))
    rows, used_pairs = [], set()
    for pay in payments:
        pid, _aid, cp, _vd, _ccy, pamount, pdir, _method, pstatus = pay
        if pstatus != "settled":
            continue
        bucket = inv_by.get((cp, compat[pdir]))
        if not bucket:
            continue
        iid, iamount, istatus = bucket.pop()
        if (pid, iid) in used_pairs:
            continue
        used_pairs.add((pid, iid))
        allocated = iamount if istatus == "paid" else max(1, iamount // 2)
        rows.append((pid, iid, min(allocated, pamount)))
    return rows


def gen_balances(rng: random.Random, accounts: list[tuple]) -> list[tuple]:
    rows = []
    for a in accounts:
        aid, _entity, _ccy, _iban, _opened, is_active = a
        if not is_active:
            continue
        balance = rng.randint(500_000, 5_000_000) * 100
        for d in (START + dt.timedelta(days=i) for i in range(DAYS)):
            balance = max(0, balance + rng.randint(-200_000, 220_000) * 100)
            rows.append((aid, d, balance))
    return rows


def main() -> None:
    rng = random.Random(SEED)
    path = db_path()
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(path))
    con.execute(SCHEMA)

    counterparties = gen_counterparties(rng)
    accounts = gen_accounts(rng)
    gl = gen_gl_accounts()
    fx = gen_fx(rng)
    invoices = gen_invoices(rng)
    payments = gen_payments(rng, accounts)
    allocations = gen_allocations(invoices, payments)
    balances = gen_balances(rng, accounts)

    inserts = [
        ("counterparty", counterparties, 6),
        ("bank_account", accounts, 6),
        ("gl_account", gl, 3),
        ("fx_rate", fx, 4),
        ("invoice", invoices, 8),
        ("payment", payments, 9),
        ("payment_allocation", allocations, 3),
        ("cash_balance_daily", balances, 3),
    ]
    for table, rows, ncols in inserts:
        placeholders = ", ".join(["?"] * ncols)
        con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)

    print(f"Built {path} (seed={SEED})")
    for table, _rows, _ in inserts:
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<20} {count:>6} rows")
    con.close()


if __name__ == "__main__":
    main()
