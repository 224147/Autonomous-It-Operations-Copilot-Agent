# Security

## Authentication & authorisation

* **JWT** (HS256, short-lived access token 30 min + rotating refresh token 7 days). Refresh tokens are single-use; logout and
  rotation put the token `jti` on a Redis deny-list until expiry. `JWT_SECRET` (≥ 32 chars) is mandatory in production -
  the process refuses to start without it.
* **Passwords**: bcrypt, ≥ 10 characters with letters and digits, ≤ 72 bytes; constant-time-ish verification for unknown
  users; lock-out after 5 failures / 15 min (per account, in Redis → 429).
* **RBAC** (`viewer < operator < approver < admin`), enforced server-side by dependencies in `backend/deps.py`:

  | Capability | Minimum role |
  |---|---|
  | Read incidents, RCA, services, alerts, analytics, knowledge, approvals | viewer |
  | Create/analyse/resolve incidents, ack alerts, RCA feedback, chat, tickets | operator |
  | Approve/reject remediation, execute a manual action, add/delete runbooks | approver |
  | Users, audit log, service inventory | admin |

* **Ingestion** endpoints accept an `X-API-Key` (one of `INGEST_API_KEYS`, constant-time comparison) or an operator+ JWT.
* **Azure Entra ID SSO**: `POST /api/v1/auth/sso/entra` validates the Entra `id_token` (RS256 signature via the tenant JWKS,
  audience = `ENTRA_CLIENT_ID`, issuer and `tid` = `ENTRA_TENANT_ID`, expiry). Group/app-role claims map to platform roles via
  `ENTRA_ROLE_MAP`; Entra is the source of truth for roles of SSO accounts. SSO accounts cannot use password login.
* **Approval workflow**: see the remediation safety model in `architecture.md`.
* **Audit trail**: append-only, each row hashes the previous one (`GET /audit/verify` detects edits/deletes). Secrets are
  scrubbed from audit details and logs.

## Secrets & configuration

* No secret is hardcoded; everything comes from environment variables / Kubernetes Secrets / **HashiCorp Vault**
  (`VAULT_ADDR` + `VAULT_TOKEN` or Kubernetes auth `VAULT_K8S_ROLE`; keys at `secret/data/<VAULT_SECRET_PATH>` are
  upper-case setting names and override the environment). In production a Vault failure aborts startup.
* `Settings.validate_for_runtime()` rejects unsafe production config (short/missing `JWT_SECRET`, SQLite, wildcard CORS).
* `.env` is git-ignored; `.env.example` documents every variable. `deployment/kubernetes/11-secrets.example.yaml` is a template.

## Zero-trust / hardening measures

* Containers run as non-root with read-only root filesystem, all capabilities dropped, seccomp `RuntimeDefault`,
  `no-new-privileges`; namespace enforces Pod Security `restricted`.
* Default-deny `NetworkPolicy` with explicit allow rules; least-privilege RBAC for the remediation service account
  (only the verbs the engine needs, only in the allowed namespaces).
* Security headers on every response (`nosniff`, `X-Frame-Options: DENY`, `no-referrer`, `Cache-Control: no-store` for API),
  restrictive CORS (explicit origins, no credentials), request-size limit on raw-body ingestion (10 MB), pagination caps,
  Pydantic validation on every body, parameterised SQL via SQLAlchemy.
* PromQL/LogQL templates only substitute validated service metadata (`^[A-Za-z0-9_.-]*$`) - no injection through service names.
* LLM prompts treat retrieved data as data; the chat assistant is read-only and cannot trigger actions.
* CI: Bandit, pip-audit, Trivy (filesystem + images), optional SonarQube; Docker image is built from slim base, pinned deps.

## Known limitations / recommendations

* Access tokens are bearer tokens held in browser memory; refresh tokens in `sessionStorage` (XSS would expose them). Serve the
  UI with the provided CSP and consider moving to HttpOnly cookies if your threat model requires it.
* `restart_vm` supports AWS EC2 only (credentials via the pod's IAM role / IRSA); Azure/GCP VM restarts are not implemented.
* The audit hash chain is tamper-*evident*, not tamper-proof: export audit rows to immutable storage (WORM/SIEM) for compliance.
* Terraform state holds secret values - use an encrypted remote backend (`backend.tf.example`).
