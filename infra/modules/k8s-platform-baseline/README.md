# k8s-platform-baseline

Cluster-wide baseline for the load-testing platform. Applied once per shared cluster
(locally by `scripts/kind-up.sh` via `infra/envs/local-kind`; in the cloud by the
cluster's own environment).

It creates:

- **`loadtest-system` namespace** (Pod Security `restricted`). It is *not* labelled
  `loadtest.platform/managed`, so neither the janitor nor the image policy touch it.
- **Janitor** — a CronJob (`python:3.12-alpine`) running `engine/janitor/janitor.py`
  from a ConfigMap mounted at `/janitor`. It deletes managed test namespaces whose
  `loadtest.platform/expires-at` annotation is in the past, so environments disappear
  even if the CI runner was killed mid-test. Its ServiceAccount may only `list` and
  `delete` namespaces. Runs as UID 65534 with a read-only root filesystem, no
  capabilities, `RuntimeDefault` seccomp and explicit resources.
- **Image admission policy** — a `ValidatingAdmissionPolicy` + binding
  (`admissionregistration.k8s.io/v1`, Kubernetes ≥ 1.30) that rejects Pod `CREATE` in
  namespaces labelled `<managed_label>=true` unless every container and init container
  image starts with one of `allowed_image_prefixes`.

## Inputs

| Name | Default | Description |
|---|---|---|
| `namespace` | `loadtest-system` | Namespace for the janitor. |
| `janitor_schedule` | `*/5 * * * *` | Janitor cron schedule. |
| `janitor_script` | — (required) | Source of `janitor.py`. |
| `allowed_image_prefixes` | — (required) | Allowed image prefixes; mirror `ALLOWED_REGISTRIES` (as `host/`) and `PLATFORM_IMAGES` in `engine/cli/src/loadtest/limits.py` (guarded by `engine/cli/tests/test_infra_sync.py`). |
| `managed_label` | `loadtest.platform/managed` | Label marking ephemeral test namespaces. |

## Outputs

| Name | Description |
|---|---|
| `namespace` | The platform namespace. |

## Usage

```hcl
module "platform_baseline" {
  source         = "../../modules/k8s-platform-baseline"
  janitor_script = file("${path.module}/../../../engine/janitor/janitor.py")
  allowed_image_prefixes = [
    "localhost:5001/",
    "docker.io/library/postgres:16-alpine",
    "docker.io/wiremock/wiremock:3.9.1",
    "docker.io/grafana/k6:0.54.0",
  ]
}
```

The `kubernetes_manifest` resources need the cluster reachable at plan time.
