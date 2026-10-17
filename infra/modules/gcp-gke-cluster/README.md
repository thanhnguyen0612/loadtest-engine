# gcp-gke-cluster

Private regional GKE cluster for load tests.

- **Dataplane V2** (`ADVANCED_DATAPATH`), which enforces Kubernetes NetworkPolicy.
- **Workload Identity** (`<project>.svc.id.goog`), node metadata served by the GKE metadata server.
- Private nodes; the API endpoint is public but restricted to `master_authorized_cidrs`
  (at least one is required, for example self-hosted CI runners).
- Release channel `REGULAR`, shielded nodes, no client certificates.
- The default node pool is removed; nodes come from `gcp-loadtest-nodepool`.

## Inputs

| Name | Default | Description |
|---|---|---|
| `project_id` | — (required) | Dedicated load-test project. |
| `region` | — (required) | Region of the regional cluster. |
| `name` | `loadtest` | Cluster name. |
| `network_id`, `subnet_id` | — (required) | From `gcp-network`. |
| `pods_range_name`, `services_range_name` | — (required) | From `gcp-network`. |
| `master_ipv4_cidr_block` | `172.16.0.0/28` | Control plane range. |
| `master_authorized_cidrs` | — (required) | Name => CIDR map allowed to reach the API. |
| `deletion_protection` | `true` | Guard against accidental destroy. |
| `labels` | `{}` | Resource labels (chargeback). |

## Outputs

| Name | Description |
|---|---|
| `cluster_name` | Cluster name. |
| `endpoint` | Kubernetes API endpoint. |
| `location` | Cluster location. |

## Usage

```hcl
module "cluster" {
  source                  = "../../modules/gcp-gke-cluster"
  project_id              = var.project_id
  region                  = var.region
  network_id              = module.network.network_id
  subnet_id               = module.network.subnet_id
  pods_range_name         = module.network.pods_range_name
  services_range_name     = module.network.services_range_name
  master_authorized_cidrs = { ci = "203.0.113.0/24" }
}
```
