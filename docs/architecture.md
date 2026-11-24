# Architecture

## Component map

```
                        ┌──────────────────────────────────────────────────────────────┐
 Browser  ──HTTPS──▶ NGINX / Ingress ──▶ React SPA (nginx)                               │
                        │      └─ /api, /ws ──▶ FastAPI API gateway (backend/)           │
                        │                         │ REST + Socket.IO (real-time events)  │
                        │        ┌────────────────┼───────────────────────────────┐      │
                        │        ▼                ▼                               ▼      │
                        │   Services layer   LangGraph orchestrator         Background    │
                        │   (services/)      (workflows/)                   scheduler     │
                        │   incident, rca,   13-node agent graph            (monitoring/) │
                        │   agent, knowledge,     │                         poll + sweep  │
                        │   remediation,          ▼                                       │
                        │   reporting, user,   Agents (agents/)  ──▶ RAG (rag/) ─▶ ChromaDB
                        │   auth, chat            │                                       │
                        │        │                ▼                                       │
                        │        │         Remediation engine ──▶ Kubernetes / AWS / ITSM │
                        │        ▼                                                        │
                        │   PostgreSQL (incidents, RCA, alerts, inventory, users, audit)  │
                        │   Redis (agent state, cache, sessions, workflow queue, signals) │
                        └──────────────────────────────────────────────────────────────┘
 Collectors / CI ──▶ /api/v1/ingest/* (logs, metrics, traces, alerts, deployments)
 Prometheus / Loki ◀── pulled by monitoring/*connectors (optional)
```

## Layout (mirrors the project structure in README.txt)

| Folder | Responsibility |
|---|---|
| `backend/` | FastAPI app factory, config, DB models, security, DI container, API routers, Socket.IO event bus, Prometheus metrics |
| `agents/<name>_agent/` | The eleven agents (+ normalization / severity helpers). `agents/common` holds the taxonomy, ports, LLM client; `agents/evaluation` the scenario generator and accuracy harness |
| `rag/` | ChromaDB knowledge base, hybrid search, embeddings (Azure OpenAI / OpenAI `text-embedding-3-large`, or local hashing fallback), document loader |
| `workflows/` | LangGraph state + graph (`incident_workflow.py`) |
| `services/` | Incident, RCA, Agent, Knowledge, Remediation, Reporting, User, Auth services (+ audit, inventory, chat) |
| `monitoring/` | Log / metric / trace / alert ingestion (parsers + Prometheus & Loki connectors), signal store, incident trigger, scheduler, service health |
| `infrastructure/` | Kubernetes, AWS EC2, Redis-cache and ITSM integrations used by the remediation engine |
| `deployment/` | Dockerfile, Kubernetes manifests, Helm chart, Terraform, CI/CD helpers, Prometheus/Grafana config |
| `knowledge_base/` | Seed runbooks, SOPs, postmortems and infrastructure docs (executable runbooks declare `steps`) |
| `frontend/` | React + TypeScript SPA |
| `tests/` | pytest suite |

## The agent workflow

`workflows/incident_workflow.py` compiles this LangGraph state graph:

```
START → monitoring → normalization → incident_detection ─(no incident)→ reporting
      → severity → correlation → rca → knowledge → recommendation → human_approval
      → remediation → verification ─(unhealthy & attempts left)→ remediation
      → learning → reporting → END
```

* **Pause/resume for human approval.** `human_approval` creates an `ApprovalRequest` and the run *ends* with
  status `awaiting_approval`. The persisted state (`incidents.workflow_state`) is re-entered later through the
  conditional entry edge (`resume_stage="approval"`) after an approver decides. State survives restarts and works
  across replicas (no in-process checkpointer).
* **Fault containment.** Every node is wrapped (`safe_node`): exceptions are recorded in `state.errors`, counted in
  metrics, and routed to `reporting`; non-critical nodes (correlation, knowledge, verification, learning) degrade instead
  of aborting.
* **Retries.** If verification reports `unhealthy`/`failed`, the next approved recovery action runs (bounded by
  `MAX_REMEDIATION_ATTEMPTS`).

### Agent logic

| Agent | How it works |
|---|---|
| Monitoring | Pulls logs/metrics/traces from the signal store (and Prometheus/Loki connectors) for the service, its dependencies and dependants, plus open alerts |
| Normalization | Canonical schema: lower-cased services, metric-name aliases, severity inference from text/failure signatures, de-duplication, NaN/invalid rejection |
| Incident detection | Metric thresholds (warning/critical), sustained-breach logic, baseline z-score anomalies, 11 log failure signatures, trace error ratios, firing alerts → weighted anomaly score; incident when score ≥ 3 |
| Severity | P1–P4 from anomaly severity/count, service **tier** from the inventory, blast radius and user impact |
| Correlation | Dependency graph walk (root candidates = affected services with no affected dependency), failing client spans and log mentions → unmonitored suspects, deployments in the 60 min before onset, merged timeline |
| RCA | Evidence-weighted scoring over a 15-category taxonomy, deployment/traffic-surge factors, calibrated confidence (score and margin vs. runner-up), alternatives; optional LLM-written explanation constrained to the evidence |
| Knowledge | Hybrid (semantic + BM25, RRF fusion) search with metadata filters → similar incidents, runbooks, SOPs, postmortems; boosts RCA confidence on confirmed history |
| Recommendation | Category→action priors, ranked by `0.5·prior + 0.3·learned success rate + 0.2·RCA confidence − risk penalty`; rollback only when a deployment is implicated; adds a ticket for P1/P2 |
| Approval | All recovery actions need a human decision (approver role). `AUTO_APPROVE_MAX_RISK=low` auto-approves low-risk actions only when the top-ranked action is low-risk |
| Remediation | Executes one approved recovery action per pass through the remediation engine (+ tickets) |
| Verification | Polls fresh metrics after the action; `healthy` / `unhealthy` / `unknown` (no fresh data) / `simulated` (dry-run) |
| Learning | Upserts the incident (category, root cause, actions, outcome) into the vector DB and updates per-(category, action) success counters used by the recommender |
| Reporting | Markdown + structured incident/RCA report, merged signal + workflow timeline, lessons learned |

`agents/common/ports.py` defines the interfaces the agents depend on (platform data, approvals, remediation, stats),
so agents are pure and unit-testable (`agents/evaluation/fakes.py`).

## Remediation engine safety model (`services/remediation_service.py`)

1. Fixed catalogue of 10 actions - there is **no** arbitrary command execution.
2. Every non-ticket action must reference an **approved** `ApprovalRequest` containing the exact action id, name and target
   (and the approver's selected subset). Verified again inside the engine regardless of the caller.
3. Target must exist in the service inventory and in `REMEDIATION_ALLOWED_NAMESPACES`.
4. `REMEDIATION_MODE=dry_run` (default) records what *would* happen; `live` is required to touch infrastructure.
5. Per-(action, target) cooldown in live mode prevents flapping; replica count is bounded (1–50, ±5 per action); cache
   clearing is prefix-scoped and never flushes.
6. Runbook steps are validated against the same catalogue; runbooks can only be added by approvers.
7. Everything is written to the hash-chained audit log and the `remediation_executions` table.

## Data model (PostgreSQL)

`users`, `services` (inventory incl. tier, dependencies, workload references), `alerts`, `incidents` (+ JSON detection /
correlation / recommendations / `workflow_state`), `rca_reports` (+ responder feedback), `approval_requests`,
`remediation_executions`, `tickets`, `deployments`, `audit_logs` (hash chain). Migrations: `alembic/`.

Redis keys: `sig:*` (recent signals), `learn:action:*` (success counters), `chat:*`, `revoked:*` (token deny-list),
`login:fail:*`, `lock:incident:*`, `sched:*` (scheduler leader locks), `workflow:jobs` (queue). Without `REDIS_URL` an
in-process implementation is used (single replica only).

## Scaling & availability

* API/workers are stateless; N replicas share Postgres + Redis + Chroma server. A Redis lock per incident prevents two
  workers processing the same incident; scheduled loops run on one replica per interval.
* Socket.IO fans out across replicas with the Redis manager; the ingress uses cookie affinity for the long-polling fallback.
* Probes: `/health/live`, `/health/ready` (database + Redis; RAG degrades gracefully). Rolling updates with `maxUnavailable: 0`, PDB, HPA.
