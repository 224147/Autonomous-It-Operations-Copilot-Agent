---
id: rb-db-connections
type: runbook
title: Runbook - Database connection pool exhaustion
category: db_connectivity
steps:
  - action: restart_service
  - action: open_ticket
---
## Symptoms
Errors like `too many connections`, `connection pool exhausted`, `timeout acquiring connection`; latency spikes on
services that depend on the database.

## Resolution
1. Restart the application service to release leaked connections.
2. If the database itself is unresponsive, escalate to the DBA and consider restarting the database (high risk, requires approval).
3. Review connection pool sizing and slow queries afterwards.
