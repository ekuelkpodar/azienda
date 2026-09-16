# Azienda — common tasks
.PHONY: lint format typecheck test test-integration build up down migrate seed clean

lint:
	cd backend && ruff check app tests

format:
	cd backend && ruff format app tests

typecheck:
	cd backend && mypy app

test:
	cd backend && pytest -q

test-integration:
	cd backend && pytest -q -m integration

migrate:
	cd backend && alembic upgrade head

seed:
	cd backend && python -m app.cli seed dev

build:
	docker compose build

up:
	docker compose up --build

down:
	docker compose down

fe-install:
	cd frontend && npm install

fe-build:
	cd frontend && npm run build

fe-typecheck:
	cd frontend && npx tsc --noEmit

clean:
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
