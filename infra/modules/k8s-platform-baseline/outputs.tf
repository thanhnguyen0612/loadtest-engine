output "namespace" {
  description = "Namespace holding the platform components."
  value       = kubernetes_namespace_v1.system.metadata[0].name
}
