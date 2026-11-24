---
id: rb-disk-full
type: runbook
title: Runbook - Disk full / no space left on device
category: disk_full
steps:
  - action: open_ticket
---
## Symptoms
`no space left on device`, failed writes, database refusing to start, disk usage above 90%.

## Resolution
1. Identify the largest directories (logs, temp files, WAL archives).
2. Rotate or purge logs and temp files; extend the volume if usage is organic growth.
3. Open a capacity ticket for permanent remediation.
