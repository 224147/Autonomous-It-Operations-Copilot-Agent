---
id: pm-orders-db
type: postmortem
title: Postmortem - orders-service database connection exhaustion
category: db_connectivity
service: orders-service
resolution: restart_service
---
## Summary
orders-service returned 503s because the PostgreSQL connection pool was exhausted by a leaked-connection bug.

## Resolution
Restarting orders-service (restart_service) released connections; the leak was fixed in the following release.
