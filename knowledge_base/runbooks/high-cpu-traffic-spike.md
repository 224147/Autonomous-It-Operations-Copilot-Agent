---
id: rb-cpu-saturation
type: runbook
title: Runbook - CPU saturation and traffic spikes
category: cpu_saturation
steps:
  - action: scale_deployment
    params: {replicas_delta: 2}
---
## Symptoms
CPU above 90%, throttling, rising p99 latency, request rate well above baseline.

## Resolution
1. Scale the deployment out (HPA may lag behind sudden spikes).
2. Verify latency returns to baseline; if CPU stays high with flat traffic, profile for a hot loop and roll back the last release.
