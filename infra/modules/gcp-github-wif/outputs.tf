output "workload_identity_provider" {
  description = "Full provider name for google-github-actions/auth `workload_identity_provider`."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "service_account_email" {
  description = "Deployer service account for google-github-actions/auth `service_account`."
  value       = google_service_account.deployer.email
}
