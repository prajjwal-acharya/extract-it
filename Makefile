.PHONY: up down test migrate seed lint

up:
	docker compose up -d

down:
	docker compose down -v

test:
	pytest tests/ -m "not live" -v

test-live:
	pytest tests/ -v

migrate:
	alembic upgrade head

seed:
	python scripts/seed_db.py

lint:
	ruff check . && mypy .

gcp-sim:
	docker compose -f docker-compose.gcp-sim.yml up -d

demo:
	python scripts/run_local_demo.py
