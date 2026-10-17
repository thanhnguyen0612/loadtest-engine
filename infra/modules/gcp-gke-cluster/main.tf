resource "google_container_cluster" "this" {
  project  = var.project_id
  name     = var.name
  location = var.region

  network    = var.network_id
  subnetwork = var.subnet_id

  # Node pools are managed separately (gcp-loadtest-nodepool); GKE requires one
  # initial node, which is removed straight away.
  remove_default_node_pool = true
  initial_node_count       = 1

  # Dataplane V2 (eBPF/Cilium) enforces Kubernetes NetworkPolicy; do not also set network_policy.
  datapath_provider = "ADVANCED_DATAPATH"

  enable_shielded_nodes = true
  deletion_protection   = var.deletion_protection
  resource_labels       = var.labels

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_range_name
    services_secondary_range_name = var.services_range_name
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = var.master_ipv4_cidr_block
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_cidrs
      content {
        display_name = cidr_blocks.key
        cidr_block   = cidr_blocks.value
      }
    }
  }

  master_auth {
    client_certificate_config {
      issue_client_certificate = false
    }
  }

  node_config {
    # Only applies to the temporary default pool; real nodes use the dedicated pool.
    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }
}
