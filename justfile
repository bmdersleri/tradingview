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
