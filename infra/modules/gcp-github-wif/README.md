# gcp-github-wif

Keyless GitHub Actions → GCP access through Workload Identity Federation (no service
account keys).

- Pool and OIDC provider (`token.actions.githubusercontent.com`) with an `attribute_condition`
  pinned to `github_repository_owner` and to `platform_workflow_ref` (the OIDC
  `job_workflow_ref` claim). Tokens from other orgs, and from any job that is not the
  platform's reusable workflow at its protected ref, are rejected outright. A service repo's
  own workflow, or a PR that edits a copy of the platform workflow, cannot get cluster
  credentials, so it cannot skip the in-namespace guardrails the platform client creates.
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
| `allowed_repositories` | — (required) | `owner/name` repositories that may impersonate the deployer (callers of the platform workflow). |
| `platform_workflow_ref` | — (required) | `owner/repo/.github/workflows/<file>.yml@refs/tags/<tag>`: the only workflow that may authenticate. Protect the ref. |
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
  platform_workflow_ref   = "acme/loadtest-engine/.github/workflows/loadtest-reusable.yml@refs/tags/v1"
  cluster_name            = module.cluster.cluster_name
}
```
