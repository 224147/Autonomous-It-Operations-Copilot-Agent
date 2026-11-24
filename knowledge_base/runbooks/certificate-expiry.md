---
id: rb-certificate
type: runbook
title: Runbook - TLS certificate expiry or handshake failures
category: certificate_issue
steps:
  - action: open_ticket
---
## Symptoms
`x509: certificate has expired`, TLS handshake errors between services or at the ingress.

## Resolution
Renew the certificate (cert-manager Certificate resource or the CA portal), then restart the affected ingress or service.
