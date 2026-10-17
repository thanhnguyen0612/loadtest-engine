variable "project_id" {
  description = "Dedicated load-test GCP project; the only place the deployer has access."
  type        = string
}

variable "github_repository_owner" {
  description = "GitHub organisation (or user) whose workflows may authenticate; pinned in the provider attribute_condition."
  type        = string
}

variable "allowed_repositories" {
  description = "Repositories (\"owner/name\") allowed to impersonate the deployer; all must belong to github_repository_owner."
  type        = list(string)

  validation {
    condition     = length(var.allowed_repositories) > 0
    error_message = "At least one repository is required."
  }

  validation {
    condition     = alltrue([for r in var.allowed_repositories : can(regex("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", r))])
    error_message = "Each repository must look like \"owner/name\"."
  }
}

variable "cluster_name" {
  description = "Name of the load-test cluster the deployer is for (output of gcp-gke-cluster)."
  type        = string
}

variable "pool_id" {
  description = "ID of the workload identity pool."
  type        = string
  default     = "github-actions"
}
