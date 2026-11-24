---
id: rb-deployment-regression
type: runbook
title: Runbook - Deployment regression
category: deployment_regression
steps:
  - action: rollback_deployment
  - action: open_ticket
---
## Symptoms
Error rate or latency increases within minutes of a release.

## Resolution
1. Roll back to the previous known-good version.
2. Open a ticket linking the failing commit and incident for the owning team.
