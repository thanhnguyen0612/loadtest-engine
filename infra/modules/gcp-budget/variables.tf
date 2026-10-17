variable "billing_account" {
  description = "Billing account ID (format 000000-000000-000000)."
  type        = string
}

variable "project_id" {
  description = "Dedicated load-test GCP project the budget is scoped to."
  type        = string
}

variable "amount_usd" {
  description = "Monthly budget in USD; alerts fire at 50%, 90% and 100% of it."
  type        = number
  default     = 300

  validation {
    condition     = var.amount_usd > 0
    error_message = "amount_usd must be positive."
  }
}

variable "notification_channels" {
  description = "Cloud Monitoring notification channel IDs (projects/P/notificationChannels/N) to alert; billing admins are always notified."
  type        = list(string)
  default     = []
}
