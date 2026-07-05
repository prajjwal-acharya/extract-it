.PHONY: up down logs test test-live test-smoke migrate seed lint format gcp-sim checkpointer dashboard

# ── Infrastructure ────────────────────────────────────────────────────────────

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f

# ── Database ──────────────────────────────────────────────────────────────────

migrate:
	docker compose exec app alembic -c infra/migrations/alembic.ini upgrade head

checkpointer:
	docker compose exec app python -c "\
from langgraph.checkpoint.postgres import PostgresSaver; \
from config.settings import settings; \
raw = settings.DATABASE_URL.replace('postgresql+psycopg://', 'postgresql://'); \
[cp.setup() or print('ok') for cp in [PostgresSaver.from_conn_string(raw).__enter__()]]"

seed:
	python scripts/seed_db.py

# ── Tests ─────────────────────────────────────────────────────────────────────

test:
	pytest tests/ -m "not live" -v

test-live:
	pytest tests/ -v

test-smoke:
	pytest frontend/tests/ -v

# ── Code quality ──────────────────────────────────────────────────────────────

lint:
	ruff check . && mypy .

format:
	ruff format .

# ── Local dev ─────────────────────────────────────────────────────────────────

dashboard:
	API_BASE_URL=http://localhost:8000 streamlit run frontend/app.py

gcp-sim:
	docker compose -f docker-compose.gcp-sim.yml up -d
