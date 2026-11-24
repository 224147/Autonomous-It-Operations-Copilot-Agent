---
id: rb-cache-failure
type: runbook
title: Runbook - Cache failure or poisoned cache
category: cache_failure
steps:
  - action: clear_cache
  - action: restart_service
---
## Symptoms
Redis connection errors, stale or corrupted responses, thundering herd on the database after cache eviction.

## Resolution
1. Clear the service cache namespace.
2. Restart the service so it re-establishes cache connections.
