variable "project_id" {
  description = "Dedicated load-test GCP project (never the prod project)."
  type        = string
}

variable "region" {
  description = "Region for the subnet, Cloud Router and NAT."
  type        = string
}

variable "name" {
  description = "Name prefix for the VPC and its companion resources."
  type        = string
  default     = "loadtest"
}

variable "subnet_cidr" {
  description = "Primary range of the GKE node subnet."
  type        = string
  default     = "10.10.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range for pod IPs."
  type        = string
  default     = "10.20.0.0/16"
}

variable "services_cidr" {
  description = "Secondary range for ClusterIP services."
  type        = string
  default     = "10.30.0.0/20"
}
