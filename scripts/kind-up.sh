#!/usr/bin/env bash
# Idempotent: bring up the shared local cluster `kind-loadtest` with a local registry on
# 127.0.0.1:5001, Calico (NetworkPolicy), metrics-server and the platform baseline (Terraform).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER=loadtest
CONTEXT="kind-${CLUSTER}"
REG_NAME=kind-registry
REG_PORT=5001
# Pinned versions (verified to exist).
NODE_IMAGE="kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
REGISTRY_IMAGE="registry:2"
CALICO_URL="https://raw.githubusercontent.com/projectcalico/calico/v3.32.2/manifests/calico.yaml"
METRICS_SERVER_URL="https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.9.0/components.yaml"

log() { printf '==> %s\n' "$*"; }
kc() { kubectl --context "$CONTEXT" "$@"; }

# 1. Local registry.
if [ "$(docker inspect -f '{{.State.Running}}' "$REG_NAME" 2>/dev/null || true)" != "true" ]; then
  docker rm -f "$REG_NAME" >/dev/null 2>&1 || true
  log "starting registry $REG_NAME on 127.0.0.1:${REG_PORT}"
  docker run -d --restart=always -p "127.0.0.1:${REG_PORT}:5000" --network bridge \
    --name "$REG_NAME" "$REGISTRY_IMAGE" >/dev/null
fi

# 2. Cluster.
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  log "creating kind cluster $CLUSTER"
  kind create cluster --name "$CLUSTER" --image "$NODE_IMAGE" \
    --config "$ROOT/scripts/kind-config.yaml" --wait 0s
fi

# 3. Map localhost:5001 -> http://kind-registry:5000 inside every node.
for node in $(kind get nodes --name "$CLUSTER"); do
  docker exec "$node" mkdir -p "/etc/containerd/certs.d/localhost:${REG_PORT}"
  printf '[host."http://%s:5000"]\n' "$REG_NAME" |
    docker exec -i "$node" cp /dev/stdin "/etc/containerd/certs.d/localhost:${REG_PORT}/hosts.toml"
done

# 4. Registry on the kind network.
if [ "$(docker inspect -f '{{json .NetworkSettings.Networks.kind}}' "$REG_NAME")" = "null" ]; then
  docker network connect kind "$REG_NAME"
fi

# 5. Document the registry (KEP-1755).
kc apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:${REG_PORT}"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
YAML

# 6. Calico CNI; nodes become Ready once it runs.
log "installing Calico"
kc apply --server-side --force-conflicts -f "$CALICO_URL" >/dev/null
kc -n kube-system rollout status daemonset/calico-node --timeout=300s
kc wait --for=condition=Ready nodes --all --timeout=300s

# 7. metrics-server (kind kubelets serve self-signed certs). Installed once: re-applying
#    the upstream manifest would drop the --kubelet-insecure-tls patch until re-patched.
if kc -n kube-system get deploy metrics-server >/dev/null 2>&1; then
  log "metrics-server already installed"
else
  log "installing metrics-server"
  kc apply -f "$METRICS_SERVER_URL" >/dev/null
  kc -n kube-system patch deploy metrics-server --type=json \
    -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
fi
kc -n kube-system rollout status deploy/metrics-server --timeout=300s

# 8. Platform baseline: janitor CronJob + image admission policy.
log "applying platform baseline (terraform)"
terraform -chdir="$ROOT/infra/envs/local-kind" init -input=false >/dev/null
terraform -chdir="$ROOT/infra/envs/local-kind" apply -input=false -auto-approve

log "cluster ready: context $CONTEXT, registry localhost:${REG_PORT}"
