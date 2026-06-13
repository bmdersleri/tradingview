set shell := ["bash", "-lc"]

install:
	pip install -e ".[dev,browser,serve,market]" && playwright install chromium

lint:
	ruff check src tests && ruff format --check src tests && mypy src

fmt:
	ruff format src tests && ruff check --fix src tests

test:
	python3 -m pytest -m "not network" --cov=tvcli --cov-fail-under=80

test-live:
	python3 -m pytest -m network -v

audit:
	! rg -n "sessionid=" tests/fixtures docs contrib .claude

reset-db:
	rm -f ~/.local/state/tvcli/cache.sqlite3
	rm -f ~/.local/share/tvcli/archive.sqlite3

serve-dev port="8789":
	python3 -m uvicorn tvcli.floatdash.app:create_app --factory --reload --port {{port}} --host 127.0.0.1

test-fast:
	python3 -m pytest tests/unit/test_chart.py tests/unit/test_freefloat_archive.py -v
