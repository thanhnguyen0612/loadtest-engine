output "node_pool_name" {
  description = "Name of the load-test node pool."
  value       = google_container_node_pool.loadtest.name
}

output "node_service_account_email" {
  description = "Service account the load-test nodes run as."
  value       = google_service_account.nodes.email
}
