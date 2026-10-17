variable "kube_context" {
  description = "kubeconfig context of the local kind cluster."
  type        = string
  default     = "kind-loadtest"
}
