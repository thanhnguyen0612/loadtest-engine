variable "project_id" {
  description = "Dedicated load-test GCP project. Must be a separate project from prod, with no VPC peering, shared VPC or VPN to it."
  type        = string
}

variable "region" {
  description = "Region of the network and the regional GKE cluster."
  type        = string
  default     = "europe-west4"
}

variable "billing_account" {
  description = "Billing account ID (format 000000-000000-000000) for the budget."
  type        = string
}

variable "budget_usd" {
  description = "Monthly budget in USD; alerts at 50%, 90% and 100%."
  type        = number
  default     = 300
}

variable "github_repository_owner" {
  description = "GitHub organisation whose workflows may deploy (Workload Identity Federation)."
  type        = string
}

variable "allowed_repositories" {
  description = "Repositories (\"owner/name\") allowed to run load tests against the cluster."
  type        = list(string)
}

variable "master_authorized_cidrs" {
  description = "Name => CIDR map allowed to reach the Kubernetes API (for example self-hosted runners)."
  type        = map(string)
}

variable "max_nodes" {
  description = "Autoscaling ceiling per zone of the spot load-test pool."
  type        = number
  default     = 6
}

variable "labels" {
  description = "Resource labels for chargeback."
  type        = map(string)
  default     = { platform = "loadtest" }
}
