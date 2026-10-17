provider "kubernetes" {
  config_path    = "~/.kube/config"
  config_context = var.kube_context
}

module "platform_baseline" {
  source = "../../modules/k8s-platform-baseline"

  janitor_script = file("${path.module}/../../../engine/janitor/janitor.py")
  # Mirrors ALLOWED_REGISTRIES and PLATFORM_IMAGES in engine/cli/src/loadtest/limits.py.
  allowed_image_prefixes = [
    "localhost:5001/",
    "docker.io/library/postgres:16-alpine",
    "docker.io/wiremock/wiremock:3.9.1",
    "docker.io/grafana/k6:0.54.0",
  ]
}

output "platform_namespace" {
  value = module.platform_baseline.namespace
}
