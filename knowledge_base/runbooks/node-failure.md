---
id: rb-node-failure
type: runbook
title: Runbook - Node or VM failure
category: node_failure
steps:
  - action: restart_vm
---
## Symptoms
Node NotReady, pods evicted, host unreachable.

## Resolution
1. Cordon and drain the node if it is still reachable.
2. Restart the VM; confirm pods are rescheduled and healthy.
