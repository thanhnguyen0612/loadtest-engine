# Cluster-wide baseline for the load-testing platform: the janitor that deletes expired
# test namespaces, and an admission policy restricting images in test namespaces.

locals {
  janitor_labels = { "app.kubernetes.io/name" = "janitor", "app.kubernetes.io/part-of" = "loadtest-platform" }
  # CEL list literal; JSON string arrays are valid CEL.
  allowed_prefixes_cel = jsonencode(var.allowed_image_prefixes)
}

resource "kubernetes_namespace_v1" "system" {
  metadata {
    name = var.namespace
    labels = {
      "pod-security.kubernetes.io/enforce"         = "restricted"
      "pod-security.kubernetes.io/enforce-version" = "latest"
    }
  }
}

# --- Janitor -------------------------------------------------------------------------

resource "kubernetes_service_account_v1" "janitor" {
  metadata {
    name      = "janitor"
    namespace = kubernetes_namespace_v1.system.metadata[0].name
    labels    = local.janitor_labels
  }
}

resource "kubernetes_cluster_role_v1" "janitor" {
  metadata {
    name   = "loadtest-janitor"
    labels = local.janitor_labels
  }

  rule {
    api_groups = [""]
    resources  = ["namespaces"]
    verbs      = ["list", "delete"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "janitor" {
  metadata {
    name   = "loadtest-janitor"
    labels = local.janitor_labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.janitor.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.janitor.metadata[0].name
    namespace = kubernetes_namespace_v1.system.metadata[0].name
  }
}

resource "kubernetes_config_map_v1" "janitor" {
  metadata {
    name      = "janitor-script"
    namespace = kubernetes_namespace_v1.system.metadata[0].name
    labels    = local.janitor_labels
  }

  data = {
    "janitor.py" = var.janitor_script
  }
}

resource "kubernetes_cron_job_v1" "janitor" {
  metadata {
    name      = "janitor"
    namespace = kubernetes_namespace_v1.system.metadata[0].name
    labels    = local.janitor_labels
  }

  spec {
    schedule                      = var.janitor_schedule
    concurrency_policy            = "Forbid"
    successful_jobs_history_limit = 3
    failed_jobs_history_limit     = 3

    job_template {
      metadata {
        labels = local.janitor_labels
      }

      spec {
        backoff_limit           = 1
        active_deadline_seconds = 120

        template {
          metadata {
            labels = merge(local.janitor_labels, {
              # Roll the pods when the script changes.
              "loadtest.platform/script-sha" = substr(sha256(var.janitor_script), 0, 16)
            })
          }

          spec {
            service_account_name = kubernetes_service_account_v1.janitor.metadata[0].name
            restart_policy       = "Never"

            security_context {
              run_as_non_root = true
              run_as_user     = 65534
              run_as_group    = 65534
              seccomp_profile {
                type = "RuntimeDefault"
              }
            }

            container {
              name    = "janitor"
              image   = "python:3.12-alpine"
              command = ["python", "/janitor/janitor.py"]

              env {
                name  = "PYTHONDONTWRITEBYTECODE"
                value = "1"
              }

              resources {
                requests = { cpu = "50m", memory = "64Mi" }
                limits   = { cpu = "200m", memory = "128Mi" }
              }

              security_context {
                run_as_non_root            = true
                run_as_user                = 65534
                allow_privilege_escalation = false
                read_only_root_filesystem  = true
                capabilities {
                  drop = ["ALL"]
                }
                seccomp_profile {
                  type = "RuntimeDefault"
                }
              }

              volume_mount {
                name       = "script"
                mount_path = "/janitor"
                read_only  = true
              }
            }

            volume {
              name = "script"
              config_map {
                name = kubernetes_config_map_v1.janitor.metadata[0].name
              }
            }
          }
        }
      }
    }
  }
}

# --- Image admission policy ----------------------------------------------------------
# Pods created in test namespaces (label managed=true) may only run images from the
# platform registry or the pinned platform images.

resource "kubernetes_manifest" "image_policy" {
  manifest = {
    apiVersion = "admissionregistration.k8s.io/v1"
    kind       = "ValidatingAdmissionPolicy"
    metadata = {
      name = "loadtest-allowed-images"
    }
    spec = {
      failurePolicy = "Fail"
      matchConstraints = {
        resourceRules = [{
          apiGroups   = [""]
          apiVersions = ["v1"]
          operations  = ["CREATE"]
          resources   = ["pods"]
        }]
      }
      variables = [
        {
          name       = "allowed"
          expression = local.allowed_prefixes_cel
        },
        {
          name       = "images"
          expression = "(object.spec.containers + (has(object.spec.initContainers) ? object.spec.initContainers : [])).map(c, c.image)"
        },
        {
          name       = "denied"
          expression = "variables.images.filter(i, !variables.allowed.exists(p, i.startsWith(p)))"
        },
      ]
      validations = [{
        expression        = "size(variables.denied) == 0"
        messageExpression = "'image(s) not allowed in load-test namespaces: ' + variables.denied.join(', ') + '; allowed prefixes: ' + variables.allowed.join(', ')"
        reason            = "Forbidden"
      }]
    }
  }
}

resource "kubernetes_manifest" "image_policy_binding" {
  manifest = {
    apiVersion = "admissionregistration.k8s.io/v1"
    kind       = "ValidatingAdmissionPolicyBinding"
    metadata = {
      name = "loadtest-allowed-images"
    }
    spec = {
      policyName        = kubernetes_manifest.image_policy.manifest.metadata.name
      validationActions = ["Deny"]
      matchResources = {
        namespaceSelector = {
          matchLabels = { (var.managed_label) = "true" }
        }
      }
    }
  }
}
