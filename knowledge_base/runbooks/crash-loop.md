---
id: rb-crash-loop
type: runbook
title: Runbook - CrashLoopBackOff after deployment
category: crash_loop
steps:
  - action: rollback_deployment
---
## Symptoms
Pods in CrashLoopBackOff, restart count increasing, readiness failing, errors starting right after a deployment.

## Resolution
1. Inspect container logs for configuration or startup exceptions.
2. Roll back the deployment to the previous ReplicaSet.
3. Open a ticket with the failing version and logs attached.
