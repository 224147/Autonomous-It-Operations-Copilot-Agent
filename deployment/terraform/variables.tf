variable "kubeconfig_path" {
  type        = string
  description = "Path to the kubeconfig of the target cluster (AKS/EKS/GKE/on-prem)."
  default     = "~/.kube/config"
}

variable "kube_context" {
  type        = string
  description = "kubeconfig context to use (null = current context)."
  default     = null
}

variable "namespace" {
  type    = string
  default = "copilot"
}

variable "host" {
  type        = string
  description = "Public host name served by the ingress."
}

variable "backend_image_repository" {
  type    = string
  default = "ghcr.io/OWNER/autonomous-it-ops-copilot-backend"
}

variable "frontend_image_repository" {
  type    = string
  default = "ghcr.io/OWNER/autonomous-it-ops-copilot-frontend"
}

variable "image_tag" {
  type        = string
  description = "Image tag to deploy (use the immutable commit SHA)."
}

variable "database_url" {
  type      = string
  sensitive = true
}

variable "redis_url" {
  type      = string
  sensitive = true
}

variable "jwt_secret" {
  type      = string
  sensitive = true
  validation {
    condition     = length(var.jwt_secret) >= 32
    error_message = "jwt_secret must be at least 32 characters."
  }
}

variable "ingest_api_keys" {
  type      = string
  sensitive = true
}

variable "extra_secrets" {
  type        = map(string)
  sensitive   = true
  default     = {}
  description = "Additional secret env vars, e.g. AZURE_OPENAI_API_KEY, BOOTSTRAP_ADMIN_PASSWORD, ENTRA_*."
}

variable "remediation_mode" {
  type    = string
  default = "dry_run"
  validation {
    condition     = contains(["dry_run", "live"], var.remediation_mode)
    error_message = "remediation_mode must be dry_run or live."
  }
}

variable "remediation_namespaces" {
  type        = list(string)
  default     = ["production"]
  description = "Namespaces the remediation engine may act on (a least-privilege Role is created in each)."
}

variable "chroma_host" {
  type    = string
  default = "chroma"
}

variable "prometheus_url" {
  type    = string
  default = ""
}

variable "loki_url" {
  type    = string
  default = ""
}
