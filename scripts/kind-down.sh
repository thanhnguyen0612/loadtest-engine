#!/usr/bin/env bash
# Delete the shared local cluster and registry. Terraform state is dropped with the cluster.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
kind delete cluster --name loadtest
docker rm -f kind-registry >/dev/null 2>&1 || true
rm -f "$ROOT"/infra/envs/local-kind/terraform.tfstate*
