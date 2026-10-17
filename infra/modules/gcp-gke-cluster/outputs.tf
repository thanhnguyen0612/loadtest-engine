output "cluster_name" {
  description = "Name of the GKE cluster."
  value       = google_container_cluster.this.name
}

output "endpoint" {
  description = "Kubernetes API endpoint."
  value       = google_container_cluster.this.endpoint
}

output "location" {
  description = "Location (region) of the cluster."
  value       = google_container_cluster.this.location
}
