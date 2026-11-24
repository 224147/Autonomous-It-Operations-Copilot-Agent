---
id: infra-architecture
type: infrastructure
title: Infrastructure - platform architecture and service dependencies
---
The checkout flow is: api-gateway -> orders-service -> payment-service -> postgres. orders-service and payment-service
cache data in redis. api-gateway is a tier-1 service; all services run in the `production` Kubernetes namespace and
are deployed via ArgoCD from the GitHub Actions pipeline.
