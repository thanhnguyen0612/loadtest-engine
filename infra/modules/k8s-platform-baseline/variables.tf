variable "namespace" {
  description = "Namespace for platform components (the janitor). Must not carry the managed label."
  type        = string
  default     = "loadtest-system"
}

variable "janitor_schedule" {
  description = "Cron schedule of the janitor that deletes expired test namespaces."
  type        = string
  default     = "*/5 * * * *"
}

variable "janitor_script" {
  description = "Source of engine/janitor/janitor.py (stdlib-only Python), mounted at /janitor."
  type        = string
}

variable "allowed_image_prefixes" {
  description = "Pods in managed namespaces may only use images starting with one of these."
  type        = list(string)

  validation {
    condition     = length(var.allowed_image_prefixes) > 0
    error_message = "At least one allowed image prefix is required."
  }
}

variable "managed_label" {
  description = "Label (value \"true\") that marks ephemeral test namespaces."
  type        = string
  default     = "loadtest.platform/managed"
}
