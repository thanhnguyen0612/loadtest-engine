# gcp-network

Isolated VPC for the load-test cluster in the dedicated load-test project.

It creates a custom-mode VPC, one node subnet with secondary ranges for pods and services
(Private Google Access, flow logs), a Cloud Router and Cloud NAT for egress (image pulls and
traffic to the services under test). There is **no peering, VPN or interconnect**, so the
network has no path to prod.

## Inputs

| Name | Default | Description |
|---|---|---|
| `project_id` | — (required) | Dedicated load-test project. |
| `region` | — (required) | Region of subnet, router and NAT. |
| `name` | `loadtest` | Name prefix. |
| `subnet_cidr` | `10.10.0.0/20` | Node range. |
| `pods_cidr` | `10.20.0.0/16` | Pod secondary range. |
| `services_cidr` | `10.30.0.0/20` | Service secondary range. |

## Outputs

| Name | Description |
|---|---|
| `network_id` | VPC ID. |
| `subnet_id` | Node subnet ID. |
| `pods_range_name` | Pod secondary range name. |
| `services_range_name` | Service secondary range name. |

## Usage

```hcl
module "network" {
  source     = "../../modules/gcp-network"
  project_id = var.project_id
  region     = var.region
}
```
