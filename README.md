# loadtest-engine

A self-service load-testing engine. A service team adds one config file (`loadtest/loadtest.yaml`) to its repo and starts a GitHub Actions workflow. The platform creates an isolated, short-lived Kubernetes namespace, deploys the service with a seeded Postgres and stubbed dependencies, runs a fixed smoke → load procedure with k6, reports the results in GitHub, and always tears the namespace down.

The prototype runs end to end on a local [kind](https://kind.sigs.k8s.io/) cluster (on a laptop and inside GitHub Actions). The GCP version of the platform is written in Terraform and validated in CI, but never applied (no cloud account). See [WRITEUP.md](WRITEUP.md) for the reasoning and [AI-USAGE.md](AI-USAGE.md) for how it was built.

## Prerequisites

| Tool | Version used | Notes |
|---|---|---|
| Docker | Colima on macOS, 4 CPU / 8 GB | Any Docker daemon works |
| kind | v0.33 | Kubernetes v1.37 node image, pinned in `scripts/kind-up.sh` |
| kubectl | recent | |
| helm | v4.3 | |
| terraform | 1.16 | Applies the in-cluster platform baseline |
| Python | 3.12 | `make venv` uses `python3.12`; override with `PYTHON=...` |

The workflows run on the `ubuntu-24.04` GitHub-hosted runner image (pinned; Python 3.12, Docker and kubectl preinstalled).

`make up` also needs internet access: it downloads Calico, metrics-server and the kind/registry images.

## Quickstart (laptop)

```bash
git clone https://github.com/thanhnguyen0612/loadtest-engine.git && cd loadtest-engine
make venv                          # .venv with the `loadtest` CLI, plus the sample service's venv
make test                          # unit tests and linters
make up                            # kind cluster, registry on localhost:5001, Calico, metrics-server, platform baseline
make image loadtest MAX_CONCURRENT=1
```

`make loadtest` runs the sample service's test (`services/sample-api/loadtest/loadtest.yaml`, size S: 50 rps for 60 s). It takes about 2 minutes and ends with a PASS/FAIL table. The exit code is 0 on pass. Results are written to `results/<RUN_ID>/` (`summary.md`, `report.html`, `k6-summary.json`). Example reports: [docs/demo/summary-pass.md](docs/demo/summary-pass.md), [docs/demo/summary-fail.md](docs/demo/summary-fail.md).

**Laptop capacity.** One test requests about 2 CPU (service 250m, Postgres 500m, WireMock 250m, k6 1000m). A 4-CPU Colima VM has about 2.8 CPU free after system pods, so only one test fits at a time. `MAX_CONCURRENT=1` sets the in-cluster concurrency cap (`LOADTEST_MAX_CONCURRENT_TESTS`) to 1, so a second run waits for a free slot. The platform default is 3, sized for the autoscaling GKE node pool. If you raise it above what the node can hold, the extra k6 pods stay `Pending` and the run ends with an ERROR report.

Make variables: `RUN_ID` (default: a timestamp), `CONFIG` (path to a `loadtest.yaml`), `TAG` (image tag, default `dev`), `MAX_CONCURRENT`. Run `make help` for the target list.

The CLI can also be used directly:

```bash
.venv/bin/loadtest validate services/sample-api/loadtest/loadtest.yaml --image localhost:5001/sample-api:dev   # resolved plan, no cluster
.venv/bin/loadtest estimate services/sample-api/loadtest/loadtest.yaml --image localhost:5001/sample-api:dev   # cost estimate
.venv/bin/loadtest teardown --namespace lt-sample-api-<run-id>
```

The developer guide (config reference, sizes, caps, results, exit codes, image requirements) is [engine/README.md](engine/README.md).

## Running in GitHub Actions

Two workflows are involved:

- `.github/workflows/loadtest-reusable.yml` (named `loadtest-reusable`) is owned by the platform team. It checks out the calling repository (config, seed, stubs, scenario) at the workspace root and the platform repository at `platform-ref` into `.platform/` (CLI, chart, k6 runner, kind scripts), so a service's PR cannot change the guardrails it is tested under. It validates the config (errors go to the job summary and the PR comment), prints a cost estimate, starts a kind cluster on the runner with `.platform/scripts/kind-up.sh`, pushes the image to the local registry, runs `loadtest run`, and then writes the job summary, uploads the `loadtest-results` artifact, comments on the PR, and tears down. These last four steps run even when the test fails. The job fails if the CLI exits non-zero, so the workflow can be a required check.
- `.github/workflows/sample-api-loadtest.yml` (named `sample-api-loadtest`) is what a service team writes: build the image, then call the reusable workflow. Here it calls the reusable workflow by local path, so `platform-repository` and `platform-ref` can stay unset (they default to this repository and commit; for a PR in this repo that means the PR's own platform code). In a real setup it lives in the team's repo and calls `uses: <org>/loadtest-engine/.github/workflows/loadtest-reusable.yml@<ref>` with `platform-repository: <org>/loadtest-engine` and `platform-ref: <ref>` (see [engine/README.md](engine/README.md#1-what-you-add-to-your-repo)). That cross-repo call is not exercised in this single-repo prototype.

How to start a run:

1. **Manual:** Actions tab → `sample-api-loadtest` → **Run workflow**.
2. **Pull request:** in https://github.com/thanhnguyen0612/loadtest-engine, add the label `loadtest` to a PR from a branch in the same repo. Every later push to that PR runs it again. PRs from forks never get a cluster.

The repository is **public** because of runner size. One test requests about 2 vCPU, and kind plus system pods need more on top. GitHub's standard Linux runners have 4 vCPU for public repos but only 2 vCPU for private ones. A private repo would need a larger (paid) runner.

`ci.yml` runs on every push and PR: ruff and pytest, `helm lint` plus chart tests, and `terraform fmt`/`validate`, tflint and trivy for every module and env.

First green end-to-end run: https://github.com/thanhnguyen0612/loadtest-engine/actions/runs/39011688212

## Repository map

```
services/sample-api/            Stands in for a team repo: FastAPI service, Dockerfile, tests
  loadtest/                     What a team owns: loadtest.yaml, seed.sql, stubs/payments-api/*.json
engine/                         Platform code (the platform team owns this)
  cli/                          `loadtest` CLI: validate, estimate, run, teardown (Python)
  chart/loadtest-env/           Helm chart for one test environment (namespace contents)
  k6/                           Platform-owned k6 runner.js and the default endpoints.js scenario
  janitor/                      Deletes expired test namespaces (runs as an in-cluster CronJob)
  README.md                     Developer guide
infra/
  modules/                      Reusable Terraform modules
    gcp-network, gcp-gke-cluster, gcp-loadtest-nodepool, gcp-budget, gcp-github-wif   (GCP, not applied)
    k8s-platform-baseline       Janitor CronJob + image admission policy (applied to kind)
  envs/gcp-loadtest/            GCP root config (validated in CI, never applied)
  envs/local-kind/              Root config applied to the local kind cluster by `make up`
scripts/                        kind-up.sh / kind-down.sh and the kind cluster config
.github/workflows/              ci.yml, loadtest-reusable.yml, sample-api-loadtest.yml
docs/demo/                      Recorded guardrail checks and example reports
docs/superpowers/               Design spec and implementation plan (see AI-USAGE.md)
```

## Guardrails

"kind" means the control is enforced in the running prototype (laptop and Actions). "GCP Terraform only" means it is written and validated but has never run.

| Concern | Control | kind | GCP Terraform only |
|---|---|---|---|
| Capacity | Dedicated spot node pool, autoscaling 0 → `max_nodes` (cluster-wide total), taint `dedicated=loadtest` | | ✅ `gcp-loadtest-nodepool` |
| Capacity | ResourceQuota per test namespace (sum of all pods + 20 %), LimitRange defaults and per-container max | ✅ | |
| Capacity | Concurrency cap: the CLI counts active managed namespaces and waits (up to 10 min) for a slot. Default 3, `LOADTEST_MAX_CONCURRENT_TESTS` overrides | ✅ | |
| Capacity | One run per service at a time (GitHub concurrency group `loadtest-<service>`) | ✅ (Actions) | |
| Sizing | S/M/L presets; custom values capped at 500 iterations/s, 900 s, 2000m CPU, 2048Mi memory | ✅ | |
| Sizing | Request-rate guard: k6 aborts a load run whose request rate exceeds 500 req/s (custom scenarios with several requests per iteration) | ✅ | |
| Sizing | Peak service CPU and memory shown next to the limits in the report | ✅ | |
| Cost | Every namespace has an `expires-at` annotation: namespace creation (after any capacity wait) + 5 min deploy + 15 s smoke + duration + 15 min buffer | ✅ | |
| Cost | Cost estimate printed before the run (simple, illustrative model) | ✅ | |
| Cost | Spot VMs, node pool scales to zero | | ✅ |
| Cost | Team and service labels on every namespace (chargeback) | ✅ | |
| Cost | Budget alerts at 50 / 90 / 100 % | | ✅ `gcp-budget` |
| Prod isolation | Default-deny NetworkPolicy (ingress and egress); only in-namespace traffic and DNS allowed | ✅ (Calico) | ✅ (Dataplane V2) |
| Prod isolation | Load targets must be paths (`/items`), never URLs; the generated scenario targets the in-namespace service. A custom `scenario.js` can name any URL, but the egress NetworkPolicy blocks everything outside the namespace except DNS | ✅ | |
| Prod isolation | Dedicated GCP project, own VPC with no peering/VPN, private nodes, API restricted to authorized CIDRs | | ✅ `gcp-network`, `gcp-gke-cluster` |
| Prod isolation | GitHub → GCP through Workload Identity Federation (no keys), pinned to the org and listed repos, `container.developer` only | | ✅ `gcp-github-wif` |
| Teardown | CLI deletes the namespace in `finally`; the workflow has an extra `if: always()` teardown step | ✅ | |
| Teardown | Janitor CronJob (every 5 min) deletes managed namespaces past `expires-at` (covers cancelled or killed runs) | ✅ | |
| Security | Validator: image must come from the allowed registry (`localhost:5001/` here) | ✅ | |
| Security | ValidatingAdmissionPolicy in test namespaces: only the allowed registry or the exact pinned postgres/wiremock/k6 images, on create, update and ephemeral containers | ✅ | |
| Security | Pod Security Standard `restricted` on test namespaces; every platform pod non-root, no privilege escalation, all capabilities dropped | ✅ | |
| Security | No real secrets: the Postgres password is the literal `loadtest`, local to a throwaway namespace | ✅ | |
| Security | Fork PRs never trigger a run | ✅ (Actions) | |

Known gaps are listed in [WRITEUP.md](WRITEUP.md#5-known-limitations).

## Demo script (for the video)

Follow [docs/demo/guardrails.md](docs/demo/guardrails.md) in order. It has the exact commands and the recorded output for each step.

1. **Bring-up and a passing run.** `make up`, show the janitor CronJob and `kubectl top nodes`, then `make image loadtest RUN_ID=pass-1`: PASS, exit 0, namespace `Terminating` straight after.
2. **Admission policy.** In a labelled probe namespace: `nginx` rejected, look-alike tag `postgres:16-alpine-evil` rejected, the exact pinned image (and its digest form) admitted, `kubectl debug` with `busybox` rejected.
3. **Network isolation.** During a run, the service cannot reach `example.com` but can reach its WireMock stub.
4. **Janitor.** Create a managed namespace with a past `expires-at`, run `make janitor-now`: it is deleted and the running test is left alone.
5. **Concurrency cap.** Two runs with `MAX_CONCURRENT=1` started 3 s apart: the second waits ("1 tests running (max 1); waiting for a slot...") and starts once the first is done.
6. **Threshold failure.** A copy of the config with `p95_ms: 1`: FAIL report, exit 1.
7. **Request-rate guard.** A custom scenario with two requests per iteration at 300 iterations/s: k6 aborts at 505 req/s, the `request rate (platform cap)` check fails, exit 1.

To finish, show a GitHub Actions run: the job summary, the `loadtest-results` artifact and, for a labelled PR, the PR comment.

## Teardown

```bash
make down    # deletes the kind cluster, the registry container and the local Terraform state
```

A single test namespace can be removed with `.venv/bin/loadtest teardown --namespace <ns>`. The janitor also removes it after `expires-at`.
