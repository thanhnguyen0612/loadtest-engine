resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = var.pool_id
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
  }

  # Rejected before any IAM check: tokens from any other GitHub org, and any job that is not
  # running the platform's reusable workflow at its protected ref. A service repo's own
  # workflows (or a PR that edits a copy of the platform workflow) cannot get credentials, so
  # the guardrails the platform client creates (quota, NetworkPolicies, Pod Security label)
  # cannot be skipped. `job_workflow_ref` is the called workflow, `repository` the caller.
  attribute_condition = join(" && ", [
    "assertion.repository_owner == \"${var.github_repository_owner}\"",
    "assertion.job_workflow_ref == \"${var.platform_workflow_ref}\"",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "loadtest-github"
  display_name = "GitHub Actions deployer for cluster ${var.cluster_name}"
}

# Deploy access to the cluster in this (dedicated) project only; no other roles.
resource "google_project_iam_member" "deployer" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "github" {
  for_each = toset(var.allowed_repositories)

  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${each.value}"
}
