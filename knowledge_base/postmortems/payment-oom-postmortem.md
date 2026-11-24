---
id: pm-payment-oom
type: postmortem
title: Postmortem - payment-service OOMKilled after v2.4.0
category: memory_exhaustion
service: payment-service
resolution: rollback_deployment
---
## Summary
payment-service pods were OOMKilled repeatedly after release v2.4.0 introduced an unbounded in-memory cache.

## Root cause
Memory leak in the new product price cache. Heap grew until the container limit was reached.

## Resolution
Rolled back to v2.3.9 (rollback_deployment), restoring service in 6 minutes. Cache was later bounded.
