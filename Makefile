.PHONY: install dev test test-pg lint scan eval migrate run simulate frontend-build docker-build compose-up helm-lint

PY ?= .venv/bin/python

install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

dev:  ## API with auto-reload, demo data and a dev admin (credentials come from your .env)
	SEED_DEMO_DATA=true $(PY) -m uvicorn backend.main:app --reload --port 8000

run:
	$(PY) -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --proxy-headers

test:
	$(PY) -m pytest tests

test-pg:  ## run the suite against PostgreSQL: make test-pg TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) $(PY) -m pytest tests

lint:
	.venv/bin/ruff check .

scan:
	.venv/bin/bandit -r backend agents rag workflows services monitoring infrastructure -c pyproject.toml -ll

eval:  ## detection / RCA / remediation-coverage on synthetic labelled scenarios
	$(PY) -m agents.evaluation.harness --seeds 5

migrate:
	.venv/bin/alembic upgrade head

simulate:  ## inject a synthetic incident: make simulate SCENARIO=memory_exhaustion (needs SIM_EMAIL/SIM_PASSWORD)
	$(PY) -m scripts.simulate --scenario $(or $(SCENARIO),memory_exhaustion) --approve

frontend-build:
	cd frontend && npm ci && npm run build

docker-build:
	docker build -f deployment/docker/Dockerfile -t autonomous-it-ops-copilot-backend:local .
	docker build -t autonomous-it-ops-copilot-frontend:local frontend

compose-up:
	docker compose --env-file .env up --build

helm-lint:
	helm lint deployment/helm/autonomous-it-ops-copilot
