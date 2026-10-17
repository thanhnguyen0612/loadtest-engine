# gcp-github-wif

Keyless GitHub Actions → GCP access through Workload Identity Federation (no service
account keys).

- Pool and OIDC provider (`token.actions.githubusercontent.com`) with an `attribute_condition`
  pinned to `github_repository_owner`, so tokens from other orgs are rejected outright.
- Service account `loadtest-github` with only `roles/container.developer`, granted in the
  load-test project (which holds nothing but this cluster).
- `roles/iam.workloadIdentityUser` for the `principalSet` of each repository in
  `allowed_repositories`.

Use the outputs with `google-github-actions/auth` (`workload_identity_provider`,
`service_account`).

## Inputs

| Name | Default | Description |
|---|---|---|
| `project_id` | — (required) | Dedicated load-test project. |
| `github_repository_owner` | — (required) | GitHub org pinned in the provider condition. |
| `allowed_repositories` | — (required) | `owner/name` repositories that may impersonate the deployer. |
| `cluster_name` | — (required) | Cluster the deployer is for (naming only). |
| `pool_id` | `github-actions` | Workload identity pool ID. |

## Outputs

| Name | Description |
|---|---|
| `workload_identity_provider` | Provider resource name for the auth action. |
| `service_account_email` | Deployer service account. |

## Usage

```hcl
module "github_wif" {
  source                  = "../../modules/gcp-github-wif"
  project_id              = var.project_id
  github_repository_owner = "acme"
  allowed_repositories    = ["acme/payments-service"]
  cluster_name            = module.cluster.cluster_name
}
```
