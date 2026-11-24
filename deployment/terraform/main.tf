terraform {
  required_version = ">= 1.6.0"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.33"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.16"
    }
  }
}

provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = var.kube_context
}

provider "helm" {
  kubernetes {
    config_path    = var.kubeconfig_path
    config_context = var.kube_context
  }
}

resource "kubernetes_namespace" "copilot" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/part-of"          = "autonomous-it-ops-copilot"
      "pod-security.kubernetes.io/enforce" = "restricted"
    }
  }
}

# Application secrets are injected from sensitive variables (CI secret store / TF_VAR_*), never stored in the repo.
# Terraform state contains these values - use an encrypted remote backend (see backend.tf.example).
resource "kubernetes_secret" "copilot" {
  metadata {
    name      = "copilot-secrets"
    namespace = kubernetes_namespace.copilot.metadata[0].name
  }
  data = merge(
    {
      DATABASE_URL    = var.database_url
      REDIS_URL       = var.redis_url
      JWT_SECRET      = var.jwt_secret
      INGEST_API_KEYS = var.ingest_api_keys
    },
    var.extra_secrets,
  )
}

resource "helm_release" "copilot" {
  name             = "copilot"
  namespace        = kubernetes_namespace.copilot.metadata[0].name
  chart            = "${path.module}/../helm/autonomous-it-ops-copilot"
  create_namespace = false
  atomic           = true
  timeout          = 600

  values = [yamlencode({
    backend        = { image = { repository = var.backend_image_repository, tag = var.image_tag } }
    frontend       = { image = { repository = var.frontend_image_repository, tag = var.image_tag } }
    existingSecret = kubernetes_secret.copilot.metadata[0].name
    config = {
      CORS_ORIGINS                   = "https://${var.host}"
      REMEDIATION_MODE               = var.remediation_mode
      REMEDIATION_ALLOWED_NAMESPACES = join(",", var.remediation_namespaces)
      CHROMA_HOST                    = var.chroma_host
      PROMETHEUS_URL                 = var.prometheus_url
      LOKI_URL                       = var.loki_url
    }
    remediation = { rbac = { create = true, namespaces = var.remediation_namespaces } }
    ingress     = { host = var.host }
  })]
}
