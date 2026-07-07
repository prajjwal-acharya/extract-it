#!/usr/bin/env bash
set -euo pipefail

echo "⚠️  This will wipe ALL data (postgres + minio volumes) and restart fresh."
read -rp "Are you sure? (yes/no): " confirm
[[ "$confirm" == "yes" ]] || { echo "Aborted."; exit 0; }

echo "→ Stopping containers and removing volumes..."
docker-compose down -v

echo "→ Starting fresh containers..."
docker-compose up -d

echo "→ Waiting for postgres to be healthy..."
until docker exec extract-it-postgres-1 pg_isready -U user -d docint &>/dev/null; do
  sleep 1
done

echo "→ Waiting for app container to be running..."
until [ "$(docker inspect -f '{{.State.Running}}' extract-it-app-1 2>/dev/null)" = "true" ]; do
  sleep 1
done
sleep 3  # give uvicorn time to finish startup

echo "→ Running migrations..."
docker exec extract-it-app-1 bash -c "
  cd /app && DATABASE_URL=postgresql+psycopg://user:password@postgres:5432/docint \
  alembic -c infra/migrations/alembic.ini upgrade head
"

echo "✓ Done. Fresh environment is up at http://localhost:8501"
