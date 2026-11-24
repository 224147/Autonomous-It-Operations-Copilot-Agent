---
id: sop-incident-management
type: sop
title: SOP - Incident management
---
## Severity definitions
- P1: complete outage or data loss of a tier-1 service; page on-call immediately.
- P2: major degradation of a tier-1 service or outage of a tier-2 service.
- P3: partial degradation with workaround.
- P4: minor issue, no customer impact.

## Process
1. Acknowledge the alert and open an incident.
2. Correlate logs, metrics, traces and recent deployments.
3. Apply the lowest-risk remediation that is documented in a runbook.
4. All remediation beyond low-risk actions requires approval from an approver.
5. Verify service health, then write the RCA and postmortem within 5 business days.
