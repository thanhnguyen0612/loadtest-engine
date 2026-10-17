variable "project_id" {
  description = "Dedicated load-test GCP project."
  type        = string
}

variable "region" {
  description = "Region of the regional cluster."
  type        = string
}

variable "name" {
  description = "Cluster name."
  type        = string
  default     = "loadtest"
}

variable "network_id" {
  description = "VPC network ID (output of gcp-network)."
  type        = string
}

variable "subnet_id" {
  description = "Node subnet ID (output of gcp-network)."
  type        = string
}

variable "pods_range_name" {
  description = "Secondary range name for pods (output of gcp-network)."
  type        = string
}

variable "services_range_name" {
  description = "Secondary range name for services (output of gcp-network)."
  type        = string
}

variable "master_ipv4_cidr_block" {
  description = "/28 for the private control plane endpoint; must not overlap any other range."
  type        = string
  default     = "172.16.0.0/28"
}

variable "master_authorized_cidrs" {
  description = "CIDRs allowed to reach the Kubernetes API (for example self-hosted CI runners)."
  type        = map(string)

  validation {
    condition     = length(var.master_authorized_cidrs) > 0
    error_message = "At least one authorized CIDR is required; the API must not be open to the internet."
  }
}

variable "deletion_protection" {
  description = "Protect the cluster from accidental terraform destroy."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Resource labels (chargeback)."
  type        = map(string)
  default     = {}
}
