---
id: sop-change-rollback
type: sop
title: SOP - Change management and rollback
---
Production changes are deployed through CI/CD only. A rollback to the previous ReplicaSet is always permitted for a
deployment that regressed within 60 minutes of release. Every deployment must be recorded in the deployment registry.
