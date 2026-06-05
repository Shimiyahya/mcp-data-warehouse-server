"""Shared fixtures: build a throwaway warehouse and point the app at it via env,
so the test suite never touches a developer's real ./warehouse.duckdb."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def warehouse_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("warehouse")
    db = tmp / "warehouse.duckdb"
    os.environ["MCP_WAREHOUSE_DB"] = str(db)
    os.environ["MCP_WAREHOUSE_AUDIT_LOG"] = str(tmp / "audit.jsonl")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed.py")],
        env=os.environ.copy(),
        cwd=str(ROOT),
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return db
