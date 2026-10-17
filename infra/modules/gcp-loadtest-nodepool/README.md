# gcp-loadtest-nodepool

Spot node pool that runs only load-test workloads.

- Spot VMs, autoscaling **0 → `max_nodes`** (a cluster-wide total, not per zone), so an idle platform costs nothing.
- Taint `dedicated=loadtest:NoSchedule` and label `workload=loadtest`; test pods opt in with
  `engine/chart/loadtest-env/values-gke.example.yaml` (`loadtest run --scheduling FILE`).
- Dedicated least-privilege node service account (log/metric writer, Artifact Registry
  reader), not the default compute service account.
- Shielded nodes, auto-repair, auto-upgrade, COS with containerd, Workload Identity metadata.

## Inputs

| Name | Default | Description |
|---|---|---|
| `project_id` | — (required) | Dedicated load-test project. |
| `location` | — (required) | Cluster location. |
| `cluster` | — (required) | Cluster name. |
| `machine_type` | `e2-standard-4` | Node machine type. |
| `max_nodes` | `6` | Autoscaling ceiling, total across zones. |
| `labels` | `{}` | Resource labels (chargeback). |

## Outputs

| Name | Description |
|---|---|
| `node_pool_name` | Node pool name. |
| `node_service_account_email` | Node service account. |

## Usage

```hcl
module "nodepool" {
  source     = "../../modules/gcp-loadtest-nodepool"
  project_id = var.project_id
  location   = module.cluster.location
  cluster    = module.cluster.cluster_name
}
```
