output "network_id" {
  description = "ID of the VPC network."
  value       = google_compute_network.this.id
}

output "subnet_id" {
  description = "ID of the node subnet."
  value       = google_compute_subnetwork.nodes.id
}

output "pods_range_name" {
  description = "Name of the secondary range for pods."
  value       = google_compute_subnetwork.nodes.secondary_ip_range[0].range_name
}

output "services_range_name" {
  description = "Name of the secondary range for services."
  value       = google_compute_subnetwork.nodes.secondary_ip_range[1].range_name
}
