# Least-privilege identity for the nodes (not the default compute service account).
resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = "${substr(var.cluster, 0, 24)}-nodes" # GCP limit: 6-30 chars
  display_name = "Load-test GKE nodes (${var.cluster})"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/artifactregistry.reader",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_node_pool" "loadtest" {
  project  = var.project_id
  name     = "loadtest-spot"
  location = var.location
  cluster  = var.cluster

  depends_on = [google_project_iam_member.nodes]

  # Scale-to-zero: no nodes (and no cost) while no test runs. ANY favours spot capacity.
  autoscaling {
    total_min_node_count = 0
    total_max_node_count = var.max_nodes
    location_policy      = "ANY"
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.machine_type
    spot            = true
    image_type      = "COS_CONTAINERD"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    # Only pods that tolerate the taint and select the label land here (see
    # engine/chart/loadtest-env/values-gke.example.yaml).
    labels          = { workload = "loadtest" }
    resource_labels = var.labels

    taint {
      key    = "dedicated"
      value  = "loadtest"
      effect = "NO_SCHEDULE"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }
}
