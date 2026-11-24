# Deployment guide

> **Verification status.** The backend was validated against PostgreSQL 16 in production mode (Alembic migration, smoke
> test, simulated incident). Helm (`helm lint` + `kubeconform`), the Kubernetes manifests (`kubeconform`), Terraform
> (`fmt`/`validate`) and the GitHub workflow (`actionlint`) were syntax/schema-validated. **No container image was built and
> nothing was deployed to a cluster** from the authoring environment (no Docker daemon or cluster was available), so the
> Docker/Kubernetes/ArgoCD paths below are unexecuted - run the smoke test (`deployment/cicd/smoke_test.sh`) after your first rollout.

## 1. Local single-host stack (Docker Compose)

```bash
cp .env.example .env     # then set at least:
# POSTGRES_PASSWORD, REDIS_PASSWORD, GRAFANA_ADMIN_PASSWORD   (compose-only)
# JWT_SECRET (>=32 chars), BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD, INGEST_API_KEYS
docker compose --env-file .env up --build
```

| URL | What |
|---|---|
| http://localhost:8080 | UI (NGINX → SPA, proxies `/api` + `/ws`) |
| http://localhost:8000/docs | API docs (only when `APP_ENV` ≠ production; compose defaults to production, set `APP_ENV=development` to enable) |
| http://localhost:3000 | Grafana (platform dashboard pre-provisioned) |
| http://localhost:9090 / :16686 | Prometheus / Jaeger |

The `migrate` service applies Alembic migrations before the backend starts. Compose runs the backend with a read-only
filesystem and dropped capabilities.

## 2. Kubernetes (plain manifests)

```bash
kubectl apply -f deployment/kubernetes/00-namespace.yaml
# create the Secret out-of-band (see 11-secrets.example.yaml for the keys), then:
kubectl apply -k deployment/kubernetes
kubectl -n copilot rollout status deploy/copilot-backend
```

Edit `10-config.yaml` (CORS origin, Prometheus/Loki URLs), `60-ingress.yaml` (host/TLS), `20-rbac.yaml` (namespaces the
remediation engine may manage) and the image names in `kustomization.yaml`. `30-datastores.yaml` is a convenience for
clusters without managed PostgreSQL/Redis - prefer managed services in production (then delete that file from the kustomization
and point `DATABASE_URL` / `REDIS_URL` at them).

## 3. Helm

```bash
kubectl create namespace copilot
kubectl -n copilot create secret generic copilot-secrets \
  --from-literal=DATABASE_URL='postgresql+psycopg://copilot:<pw>@<host>:5432/copilot' \
  --from-literal=REDIS_URL='rediss://:<pw>@<host>:6380/0' \
  --from-literal=JWT_SECRET="$(openssl rand -base64 48)" \
  --from-literal=INGEST_API_KEYS="$(openssl rand -hex 24)" \
  --from-literal=BOOTSTRAP_ADMIN_EMAIL=admin@example.com \
  --from-literal=BOOTSTRAP_ADMIN_PASSWORD='<strong-password>'
helm upgrade --install copilot deployment/helm/autonomous-it-ops-copilot -n copilot \
  --set backend.image.repository=ghcr.io/<owner>/autonomous-it-ops-copilot-backend --set backend.image.tag=<sha> \
  --set frontend.image.repository=ghcr.io/<owner>/autonomous-it-ops-copilot-frontend --set frontend.image.tag=<sha> \
  --set ingress.host=copilot.example.com --set config.CORS_ORIGINS=https://copilot.example.com \
  --set 'remediation.rbac.namespaces={production}'
```

The chart runs `alembic upgrade head` as a pre-install/pre-upgrade hook, creates a least-privilege Role/RoleBinding in each
remediation namespace, an HPA, PDB, default-deny-style NetworkPolicy and (optionally) a ServiceMonitor.
Use an existing Secret synced from Vault / External Secrets / Azure Key Vault CSI (`existingSecret`).

## 4. Terraform

`deployment/terraform` creates the namespace and Secret and installs the Helm chart on an existing cluster:

```bash
cd deployment/terraform
cp backend.tf.example backend.tf            # encrypted remote state - the Secret values land in state
export TF_VAR_database_url=... TF_VAR_redis_url=... TF_VAR_jwt_secret=... TF_VAR_ingest_api_keys=...
terraform init && terraform apply -var host=copilot.example.com -var image_tag=<sha>
```

Provisioning the cluster itself (AKS/EKS/GKE), PostgreSQL and Redis is intentionally out of scope: the target cloud is not
specified in the requirements.

## 5. CI/CD (GitHub Actions + ArgoCD)

`.github/workflows/ci.yml` implements the pipeline stages from the requirements:
checkout → unit tests (SQLite + PostgreSQL, Alembic check, evaluation gate) → lint → security scan (Bandit, pip-audit, Trivy,
SonarQube when `SONAR_HOST_URL`/`SONAR_TOKEN` are set) → Docker build → Trivy image scan + push to GHCR → deploy to
staging via ArgoCD (`deployment/cicd/argocd-applications.yaml`) → smoke test → production (GitHub *environment* with
required reviewers = manual approval).

Required repository configuration: variables `ARGOCD_SERVER`, `STAGING_URL`, `PRODUCTION_URL`, (`SONAR_HOST_URL`);
secrets `ARGOCD_AUTH_TOKEN`, `COPILOT_INGEST_API_KEY`, (`SONAR_TOKEN`); environments `staging`, `production`.
Replace `OWNER/REPO` in the ArgoCD manifests. The workflow must live at the repository root's `.github/workflows`.

## 6. Production checklist

- [ ] `APP_ENV=production`, `JWT_SECRET` ≥ 32 chars from a secret store, PostgreSQL (`DATABASE_URL`), **Redis** (`REDIS_URL`), Chroma server (`CHROMA_HOST`)
- [ ] `DB_AUTO_CREATE=false`; migrations applied (`alembic upgrade head`)
- [ ] Remove `BOOTSTRAP_ADMIN_*` after the first login; create named users (or enable Entra SSO)
- [ ] `CORS_ORIGINS` set to the real UI origin; TLS at the ingress
- [ ] `INGEST_API_KEYS` configured and given to collectors/CI
- [ ] Azure OpenAI / OpenAI configured for LLM explanations and `text-embedding-3-large` embeddings (otherwise deterministic fallbacks are used; see below)
- [ ] Service inventory populated (`PUT /api/v1/services/{name}`: tier, namespace, deployment, dependencies, cache prefix, VM id)
- [ ] Run in `REMEDIATION_MODE=dry_run` first; switch to `live` only after reviewing audit/approval flows. Review `REMEDIATION_ALLOWED_NAMESPACES` and the RBAC Role
- [ ] Prometheus scrapes `/metrics` (`ServiceMonitor` or `deployment/monitoring/prometheus.yml`); load alert rules + Grafana dashboard
- [ ] Set `OTEL_EXPORTER_OTLP_ENDPOINT` to ship the platform's own traces to Jaeger

## 7. Embeddings change = re-index

The Chroma collection name includes the embedder id (`kb_hash512`, `kb_openai_text_embedding_3_large`, `kb_azure_<deployment>`).
Switching embedders creates a fresh collection and re-seeds `knowledge_base/`; previously *learned* incidents live in the old
collection until re-indexed (resolved incidents are re-learned when re-analysed/resolved).
