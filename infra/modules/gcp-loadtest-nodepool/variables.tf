variable "project_id" {
  description = "Dedicated load-test GCP project."
  type        = string
}

variable "location" {
  description = "Location (region) of the cluster."
  type        = string
}

variable "cluster" {
  description = "Name of the GKE cluster (output of gcp-gke-cluster)."
  type        = string
}

variable "machine_type" {
  description = "Machine type of the spot nodes."
  type        = string
  default     = "e2-standard-4"
}

variable "max_nodes" {
  description = "Autoscaling ceiling for the whole pool across all zones (total_max_node_count); the pool scales to 0 when idle."
  type        = number
  default     = 6

  validation {
    condition     = var.max_nodes >= 1
    error_message = "max_nodes must be at least 1."
  }
}

variable "labels" {
  description = "Resource labels (chargeback)."
  type        = map(string)
  default     = {}
}
