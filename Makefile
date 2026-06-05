.PHONY: setup data test lint fmt demo run clean

UV ?= uv

setup:        ## install dependencies into .venv
	$(UV) sync

data:         ## (re)build the deterministic warehouse.duckdb
	$(UV) run python scripts/seed.py

test:         ## run the test suite
	$(UV) run pytest -q

lint:         ## lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt:          ## auto-format the code
	$(UV) run ruff format .
	$(UV) run ruff check . --fix

demo:         ## run the live LLM demo (needs ANTHROPIC_API_KEY)
	$(UV) run python demo.py

run:          ## run the MCP server over stdio
	$(UV) run mcp-warehouse

clean:        ## remove the generated warehouse + caches
	$(UV) run python -c "import pathlib; [p.unlink() for p in pathlib.Path('.').glob('warehouse.duckdb*')]"
