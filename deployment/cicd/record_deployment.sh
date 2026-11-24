#!/usr/bin/env bash
# Registers a deployment with the Copilot so RCA can correlate incidents with releases.
# Usage: COPILOT_URL=... INGEST_API_KEY=... bash record_deployment.sh <environment> <version>
set -euo pipefail
: "${COPILOT_URL:?}" "${INGEST_API_KEY:?}"
ENVIRONMENT="${1:?environment}"
VERSION="${2:?version}"
SERVICES="${DEPLOYED_SERVICES:-copilot-backend copilot-frontend}"
for service in $SERVICES; do
  curl -sS --fail --max-time 15 -X POST "${COPILOT_URL%/}/api/v1/deployments" \
    -H "X-API-Key: ${INGEST_API_KEY}" -H "Content-Type: application/json" \
    -d "{\"service\":\"${service}\",\"version\":\"${VERSION}\",\"environment\":\"${ENVIRONMENT}\",\"status\":\"succeeded\",\"source\":\"github-actions\",\"commit_sha\":\"${GITHUB_SHA:-}\",\"deployed_by\":\"${GITHUB_ACTOR:-ci}\"}" >/dev/null
  echo "recorded ${service}@${VERSION} (${ENVIRONMENT})"
done
