.PHONY: up down test test-live migrate seed lint gcp-sim

up:
	docker compose up -d

down:
	docker compose down -v

test:
	pytest tests/ -m "not live" -v

test-live:
	pytest tests/ -v

migrate:
	alembic -c infra/migrations/alembic.ini upgrade head

seed:
	python scripts/seed_db.py

lint:
	ruff check . && mypy .

gcp-sim:
	docker compose -f docker-compose.gcp-sim.yml up -d
