---
id: rb-memory-exhaustion
type: runbook
title: Runbook - Container memory exhaustion / OOMKilled
category: memory_exhaustion
steps:
  - action: restart_pod
  - action: scale_deployment
    params: {replicas_delta: 1}
---
## Symptoms
Pods restart with reason OOMKilled, memory usage above 90% of the limit, rising latency and 5xx errors.

## Diagnosis
1. Check `kubectl describe pod` for `Last State: Terminated (OOMKilled)`.
2. Compare memory growth with the last deployment; a leak introduced by a new release is the common cause.

## Resolution
1. Restart the affected pod to restore service immediately.
2. Scale the deployment out by one replica to spread load.
3. If the leak started after a deployment, roll back to the previous version and open a ticket for the owning team.
