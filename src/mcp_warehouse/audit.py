"""Structured audit logging.

Every tool call — allowed or denied — is recorded as one JSON object per line,
written to **stderr** (always safe for a stdio MCP server) and appended to a
JSONL file. Logging must never touch stdout: that is the JSON-RPC channel and
writing to it corrupts the protocol.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime
from typing import Any

from .config import audit_log_path

_lock = threading.Lock()


def audit(**fields: Any) -> None:
    """Append one structured audit record (stderr + JSONL file)."""
    record = {"ts": datetime.now(UTC).isoformat(), **fields}
    line = json.dumps(record, default=str, ensure_ascii=False)

    # stderr — safe and always present.
    print(line, file=sys.stderr, flush=True)

    # Append to the JSONL audit file; never let a logging failure break the call.
    try:
        path = audit_log_path()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError:
        pass
