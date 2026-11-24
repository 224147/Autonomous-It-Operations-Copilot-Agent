# Autonomous IT Operations Copilot

Production-grade, multi-agent AIOps platform that **monitors, detects, diagnoses, remediates, learns from and reports on IT
incidents**. A LangGraph orchestrator drives 11 cooperating agents over a RAG knowledge base (ChromaDB), behind a FastAPI gateway and
a React dashboard, with human approval, RBAC/JWT/SSO, a tamper-evident audit trail and Kubernetes-native deployment.

`README.txt` is the requirements specification; this file documents what was built and how to run it.
Deeper documentation: [architecture](docs/architecture.md) · [security](docs/security.md) · [monitoring integrations](docs/integrations.md) · [deployment](docs/deployment.md).

## What is implemented

| Requirement (README.txt) | Implementation |
|---|---|
| LangGraph state flow (Monitoring → … → Reporting) | `workflows/incident_workflow.py`: 13 nodes, pause/resume at **Human Approval**, retry loop after failed verification |
| 11 agents | `agents/{monitoring,incident,correlation,rca,knowledge,recommendation,approval,remediation,verification,learning,reporting}_agent/` (+ normalization & severity) |
| RAG (hybrid + semantic + metadata filter, ChromaDB, `text-embedding-3-large`) | `rag/` - Azure OpenAI / OpenAI embeddings when configured, deterministic local fallback otherwise |
| PostgreSQL / Redis | SQLAlchemy models + Alembic (`alembic/`); Redis for agent state, queue, sessions, signals, locks (in-memory fallback for dev) |
| 8 FastAPI services | Incident, RCA, Agent, Knowledge, Remediation, Reporting, User, Authentication - `services/` + `backend/api/` |
| 10 remediation actions | restart service/pod/VM/database, rollback, scale, clear cache, trigger backup, execute runbook, open ticket - `services/remediation_service.py`, `infrastructure/` |
| Security: RBAC, JWT, SSO/Entra ID, approval workflow, audit trail, secrets, Vault, zero trust | `backend/security.py`, `services/auth_service.py`, `backend/vault.py`, [docs/security.md](docs/security.md) |
| Monitoring ingestion (logs/metrics/traces/alerts, event correlation, incident trigger) | `monitoring/` - Alertmanager/Grafana, Azure Monitor, Datadog, Loki, OTLP/JSON, Prometheus & Loki pull connectors |
| React frontend (dashboards, incidents, RCA timeline, approvals, chat, analytics, alerts, audit, users, knowledge) | `frontend/` (React, TypeScript, MUI, React Query, Redux Toolkit, Recharts, Socket.IO) |
| Docker / Kubernetes / Helm / Terraform / CI-CD / ArgoCD | `deployment/`, `docker-compose.yml`, `.github/workflows/ci.yml` |
| Platform self-monitoring (Prometheus, Grafana, Loki, OpenTelemetry, Jaeger) | `/metrics`, `deployment/monitoring/`, optional OTLP tracing |
| Success-criteria measurement | `agents/evaluation/` harness + `/api/v1/reporting/kpis` (see [Honest status](#honest-status--open-items)) |

## Quick start (development)

Requirements: Python 3.11+ (tested on 3.12 and 3.14), Node 20+ for the frontend. No Docker needed for development.

```bash
make install                      # creates .venv and installs requirements-dev.txt
cp .env.example .env              # then edit: set BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD (>=10 chars, letters+digits)
SEED_DEMO_DATA=true make dev      # API on http://localhost:8000  (docs: /docs)
```

With no `REDIS_URL`, no LLM keys and the default SQLite database the platform runs fully in-process with deterministic
fallbacks. Inject a synthetic incident and watch the workflow:

```bash
SIM_EMAIL=<admin email> SIM_PASSWORD=<admin password> COPILOT_URL=http://localhost:8000 \
  python -m scripts.simulate --scenario memory_exhaustion --approve
# scenarios: memory_exhaustion cpu_saturation traffic_spike crash_loop db_connectivity disk_full dependency_failure
#            cache_failure deployment_regression config_error node_failure certificate_issue
```

Frontend:

```bash
cd frontend && npm install
VITE_PROXY_TARGET=http://localhost:8000 npm run dev      # http://localhost:5173
npm test && npm run build
```

## Configuration

All configuration is via environment variables (or `.env`); **every variable is documented in [`.env.example`](.env.example)**.
Nothing secret is stored in the repository. Key groups:

| Area | Variables |
|---|---|
| Core | `APP_ENV`, `DATABASE_URL`, `REDIS_URL`, `CHROMA_HOST`/`CHROMA_PERSIST_DIR`, `CORS_ORIGINS`, `LOG_JSON` |
| Auth | `JWT_SECRET` (required in production), `BOOTSTRAP_ADMIN_*`, `INGEST_API_KEYS`, `ENTRA_TENANT_ID`/`ENTRA_CLIENT_ID`/`ENTRA_ROLE_MAP` |
| Secrets | `VAULT_ADDR`, `VAULT_TOKEN` or `VAULT_K8S_ROLE`, `VAULT_SECRET_PATH` (Vault values override env) |
| LLM / RAG | `LLM_PROVIDER`, `AZURE_OPENAI_*`, `OPENAI_*` (chat model + `text-embedding-3-large`) |
| Remediation | `REMEDIATION_MODE` (`dry_run` default / `live`), `REMEDIATION_ALLOWED_NAMESPACES`, `AUTO_APPROVE_MAX_RISK`, `TICKET_WEBHOOK_URL`, `AWS_REGION`, `CACHE_REDIS_URL` |
| Monitoring | `PROMETHEUS_URL`, `LOKI_URL`, `DETECTION_*`, `POLL_INTERVAL_SECONDS` |

In `APP_ENV=production` the app **refuses to start** with a missing/short `JWT_SECRET`, SQLite, or wildcard CORS.

## Running tests and checks

```bash
make test         # 70+ tests: agents/workflow, API + RBAC + SSO, remediation safety, parsers/connectors, RAG, KV
make test-pg TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db   # same suite on PostgreSQL
make lint scan    # ruff, bandit
make eval         # detection / RCA / remediation-coverage on labelled synthetic scenarios
```

## API overview (`/api/v1`, OpenAPI at `/api/v1/openapi.json`)

| Group | Endpoints |
|---|---|
| Auth | `POST /auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/sso/entra`, `GET /auth/me`, `/auth/config`, `POST /auth/change-password` |
| Incident service | `GET/POST /incidents`, `GET /incidents/{id}` (detail with RCA, approvals, executions, tickets, events), `POST /incidents/{id}/analyze\|resolve`, `PATCH /incidents/{id}/status`, `GET /incidents/stats` |
| RCA service | `GET /rca`, `/rca/{id}`, `/rca/{id}/markdown`, `/rca/accuracy`, `GET /incidents/{id}/rca`, `POST /rca/{id}/feedback` |
| Agent service | `GET /agents`, `POST /agents/workflows/run`, `GET /agents/workflows/{incident_id}` |
| Knowledge service | `POST /knowledge/search`, `GET/POST /knowledge/documents`, `GET/DELETE /knowledge/documents/{id}`, `GET /knowledge/stats` |
| Remediation service | `GET /remediation/actions`, `/remediation/approvals`, `POST /remediation/approvals/{id}/decision`, `POST /remediation/execute`, `GET /remediation/executions`, `GET/POST /remediation/tickets` |
| Reporting service | `GET /reporting/incidents/{id}` (+`/markdown`), `/reporting/analytics`, `/reporting/kpis` |
| User service | `GET/POST /users`, `PATCH /users/{id}` (admin) |
| Monitoring | `POST /ingest/{logs,logs/loki,metrics,metrics/otlp,traces,traces/otlp,alerts,alerts/alertmanager,alerts/azure-monitor,alerts/datadog}`, `GET /alerts`, `POST /alerts/{id}/acknowledge\|resolve`, `GET /services`, `/services/health`, `PUT/DELETE /services/{name}`, `GET/POST /deployments` |
| Audit | `GET /audit`, `GET /audit/verify` (admin) |
| Chat | `POST /chat`, `GET /chat/{session_id}` |
| Ops | `GET /health/live`, `/health/ready`, `/metrics` (Prometheus) |

Real-time: Socket.IO at `/ws/socket.io` (auth `{token: <access token>}`); events `incident.created|updated`, `alert.received|updated`,
`approval.requested|decided`, `remediation.executed`, `ticket.created`, `deployment.recorded`.

## Incident lifecycle

1. Signals/alerts arrive (push endpoints or Prometheus/Loki polling). The **Incident Trigger** correlates across dependencies and
   opens one incident per correlated failure, then queues the workflow.
2. Agents detect → classify severity → correlate → find root cause (confidence) → retrieve history/runbooks → rank actions.
3. The run pauses at **Human Approval** (`awaiting_approval`). An *approver* approves all/part of the plan or rejects it.
4. The remediation engine executes (dry-run by default), the Verification Agent checks recovery, the Learning Agent updates the
   knowledge base, the Reporting Agent writes the RCA/incident report. Unverified/failed remediation escalates with a ticket.
5. Responders give RCA feedback → `copilot_rca_accuracy_ratio` metric and `/reporting/kpis`.

## Deployment

See [docs/deployment.md](docs/deployment.md): Docker Compose (`docker compose --env-file .env up --build`), Kubernetes manifests
(`kubectl apply -k deployment/kubernetes`), Helm chart, Terraform, GitHub Actions + ArgoCD pipeline, and the production checklist.

## Honest status / open items

**Verified in this repository (executed):** the full pytest suite (SQLite *and* PostgreSQL 16), Alembic upgrade/check/downgrade on PostgreSQL,
the API in production mode on PostgreSQL with a simulated incident end-to-end (detect → approve → remediate (dry-run) → learn),
Socket.IO events, Chroma in server mode, `ruff`, `bandit`, `helm lint`, `kubeconform` on rendered/plain manifests, `terraform validate`,
`actionlint`, and the frontend (`tsc`, `npm run build`, 12 Vitest tests, and a manual browser pass through the real UI against the
running backend: login, all 12 pages, approving a remediation plan, chat, knowledge search, live Socket.IO updates). The Azure Entra
popup login flow was not exercised in a browser, and there are no automated browser (E2E) tests.

**Not verified (no Docker daemon / cluster / cloud tenant was available):**

* Docker images were **not built** and the compose stack, Kubernetes, ArgoCD and GitHub Actions pipeline were **not run**. Nothing has been deployed.
* Live **Kubernetes remediation** (rollback/scale/restart) is unit-tested against fake client objects only; **AWS EC2 restart**, the **ITSM webhook**,
  **Azure OpenAI / OpenAI** calls, **Azure Entra ID** (tested with locally signed tokens, not a real tenant) and **Vault** (tested against a mock) were not run against real systems.
* Redis behaviour is tested through `fakeredis`, not a real server.

**Accuracy claims.** The success criteria (> 90 % detection, > 85 % RCA, > 70 % automated remediation coverage) are measured only
on *synthetic labelled scenarios authored with the rules* (`make eval`: currently 100 % / 100 % / 75 %). That proves the logic is
internally consistent and regression-protected, **not** real-world accuracy. Real accuracy must be measured from production
feedback (`POST /rca/{id}/feedback` → `/reporting/kpis`). **MTTR reduction > 50 %** needs a pre-adoption baseline that does not exist;
**99.9 % availability** must be measured by external probing of `/health/ready` against the deployed system.

**Requirements that were unclear and how they were handled** (please confirm):

1. *Target cloud / cluster* is unspecified → Terraform deploys onto an existing cluster; no cloud-specific provisioning.
2. *Splunk, ELK, Datadog/Azure Monitor/CloudWatch pulls, OTLP/protobuf, Prometheus remote-write* are not ingested natively (webhooks for Datadog/Azure Monitor/Alertmanager/Grafana and OTLP/JSON are) - see [integrations](docs/integrations.md).
3. *Restart VM* supports AWS EC2 only; Azure/GCP VM restart is not implemented.
4. *Without LLM credentials* the platform uses deterministic logic: RCA/report text is template-based, chat answers are template-based, and
   embeddings use a local lexical hashing embedder (not semantic). Configure Azure OpenAI/OpenAI for `text-embedding-3-large` and LLM-written explanations.
5. *FAISS* is listed in the stack but ChromaDB (the stated vector database) is the only vector store implemented.
6. Human approval is mandatory for all recovery actions by default; `AUTO_APPROVE_MAX_RISK=low` is opt-in.
7. Signals (logs/metrics/traces) are kept as a bounded recent window in Redis; long-term storage is delegated to Loki/Prometheus/Jaeger.
8. The "per-person" team split in `README.txt` is organisational; one codebase implements all four areas.

## Project layout

```
backend/  agents/  rag/  workflows/  services/  monitoring/  infrastructure/  knowledge_base/
frontend/  deployment/{docker,kubernetes,helm,terraform,cicd,monitoring}  docs/  tests/  scripts/  alembic/  .github/workflows/
```
