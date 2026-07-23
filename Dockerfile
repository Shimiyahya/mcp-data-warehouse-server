# ── build: install deps with uv, bake the deterministic warehouse ─────────
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependency layer first so code changes don't re-resolve the lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY README.md LICENSE ./
COPY src ./src
COPY scripts ./scripts
# --no-editable: the runtime stage copies only .venv, so the package must
# live in site-packages rather than point back at ./src.
RUN uv sync --frozen --no-dev --no-editable

# seed.py is fully deterministic (fixed RNG), so baking the DB into the
# image keeps it self-contained and byte-stable across builds.
RUN MCP_WAREHOUSE_DB=/app/warehouse.duckdb uv run --no-sync python scripts/seed.py

# ── runtime: slim image, non-root, HTTP transport ──────────────────────────
FROM python:3.12-slim-bookworm
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/warehouse.duckdb /data/warehouse.duckdb

ENV PATH="/app/.venv/bin:$PATH" \
    MCP_TRANSPORT=streamable-http \
    MCP_PORT=8000 \
    MCP_WAREHOUSE_DB=/data/warehouse.duckdb \
    MCP_WAREHOUSE_AUDIT_LOG=/tmp/audit.jsonl

USER 10001:10001
WORKDIR /app
EXPOSE 8000
CMD ["mcp-warehouse"]
