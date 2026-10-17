provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "apis" {
  for_each = toset([
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

module "network" {
  source = "../../modules/gcp-network"

  project_id = var.project_id
  region     = var.region
  name       = "loadtest"

  depends_on = [google_project_service.apis]
}

module "cluster" {
  source = "../../modules/gcp-gke-cluster"

  project_id              = var.project_id
  region                  = var.region
  name                    = "loadtest"
  network_id              = module.network.network_id
  subnet_id               = module.network.subnet_id
  pods_range_name         = module.network.pods_range_name
  services_range_name     = module.network.services_range_name
  master_authorized_cidrs = var.master_authorized_cidrs
  labels                  = var.labels
}

module "nodepool" {
  source = "../../modules/gcp-loadtest-nodepool"

  project_id = var.project_id
  location   = module.cluster.location
  cluster    = module.cluster.cluster_name
  max_nodes  = var.max_nodes
  labels     = var.labels
}

module "budget" {
  source = "../../modules/gcp-budget"

  billing_account = var.billing_account
  project_id      = var.project_id
  amount_usd      = var.budget_usd

  depends_on = [google_project_service.apis]
}

module "github_wif" {
  source = "../../modules/gcp-github-wif"

  project_id              = var.project_id
  github_repository_owner = var.github_repository_owner
  allowed_repositories    = var.allowed_repositories
  cluster_name            = module.cluster.cluster_name

  depends_on = [google_project_service.apis]
}

output "cluster_name" {
  value = module.cluster.cluster_name
}

output "workload_identity_provider" {
  value = module.github_wif.workload_identity_provider
}

output "service_account_email" {
  value = module.github_wif.service_account_email
}
