# Remote state for a real deployment: create the bucket first (versioned, uniform access), then
# uncomment and run `terraform init`. Left commented because this env is validated, not applied.
#
# terraform {
#   backend "gcs" {
#     bucket = "CHANGE-ME-loadtest-tfstate"
#     prefix = "envs/gcp-loadtest"
#   }
# }
