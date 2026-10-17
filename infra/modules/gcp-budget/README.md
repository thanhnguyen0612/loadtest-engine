# gcp-budget

Billing budget scoped to the load-test project, alerting at **50 %, 90 % and 100 %** of
`amount_usd` of current spend. Billing admins are notified by default; add Cloud Monitoring
channels with `notification_channels`. Creating a budget needs `billing.budgets.create` on
the billing account and the `billingbudgets.googleapis.com` API.

## Inputs

| Name | Default | Description |
|---|---|---|
| `billing_account` | — (required) | Billing account ID. |
| `project_id` | — (required) | Project the budget covers. |
| `amount_usd` | `300` | Monthly budget in USD. |
| `notification_channels` | `[]` | Monitoring notification channel IDs. |

## Outputs

| Name | Description |
|---|---|
| `budget_name` | Budget resource name. |

## Usage

```hcl
module "budget" {
  source          = "../../modules/gcp-budget"
  billing_account = var.billing_account
  project_id      = var.project_id
}
```
