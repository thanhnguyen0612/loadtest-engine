# Write-up: self-service ephemeral load testing

## 1. Context assumed, and why

The brief leaves the setup open. Each assumption below is typical for a company of this size and makes the design concrete:

- **~50 containerised services run on Kubernetes (GKE) in prod.** Tests then run on Kubernetes too, close to the prod deploy path.
- **One internal GitHub repo per team, no forks.** Teams already use Actions, so a workflow is the lowest-friction entry point. Fork PRs are out of scope: their image, seed SQL and k6 script would be untrusted input.
- **One company image registry; teams own their DB schema; services talk HTTP.**
- **GCP.** I have **no cloud account**. The GCP platform is Terraform checked in CI (`fmt`, `validate`, tflint, trivy), never applied. The runnable prototype uses a **kind cluster as a stand-in for the shared GKE cluster**, on a laptop and in GitHub Actions. A local registry on `localhost:5001` stands in for the company registry.

Real on kind: the CLI flow, Helm chart, quotas, NetworkPolicies (Calico), Pod Security, admission policy, janitor and concurrency cap. Terraform only: GCP project APIs, VPC, private GKE cluster, spot node pool, budget alerts, GitHub Workload Identity Federation.

## 2. Alternatives considered

**Environment provisioning**

| Option | Verdict |
|---|---|
| Namespace per test on a shared, dedicated cluster | **Picked.** Ready in about a minute, no idle cost, one place to enforce policy. |
| Cluster per test | Strongest isolation, but 5–10 min to create and a cluster fee per test. |
| VMs / serverless per test | Unlike prod; teams would need a second deploy path. |

**Load generator**

| Option | Verdict |
|---|---|
| k6 | **Picked.** One small binary, arrival-rate executors (fixed rps, not "N users"), thresholds that set the exit code, a JSON summary. Scripts are JavaScript. |
| Locust | Python, but models users rather than rps; scaling needs master/workers. |
| JMeter | XML test plans, heavy JVM, hard to review in a PR. |
| Gatling | Good reports, but Scala/Java DSL and JVM start-up. |

k6 runs **in the cluster** as a Job in the test namespace, not on the GitHub runner, so the runner's network does not skew results and all traffic stays inside the namespace. The platform owns `runner.js` (load shape, thresholds, summary); a team's optional `scenario.js` defines one iteration. The shape caps iterations/s, and a guard aborts any run whose request rate exceeds 500/s.

**IaC layout**

| Option | Verdict |
|---|---|
| Terraform for the long-lived platform, Helm chart per test | **Picked.** Terraform suits slow-changing infrastructure with state. A test environment lives for minutes and needs no state file; `helm install --wait` and a namespace delete fit that. |
| Terraform for everything | A state file per test, slow plans, and state to clean up when a run is killed. |

Terraform is split into small single-purpose modules (network, GKE cluster, node pool, budget, GitHub WIF, k8s baseline) composed by two root configs, `envs/gcp-loadtest` and `envs/local-kind`. The baseline module (janitor, admission policy) is applied to kind; it is not yet wired into `envs/gcp-loadtest`, which needs only different `allowed_image_prefixes`.

**Developer interface**

| Option | Verdict |
|---|---|
| Config file in the repo + GitHub Actions | **Picked.** Versioned with the code, reviewed in PRs, no new tool or credentials. Results land where developers already look. |
| Portal / Backstage plugin | Good for discovery, but a new service to run. A later front end over the same engine. |
| CLI on the developer's laptop | Needs cluster credentials on laptops, which conflicts with the guardrails. The CLI exists, but CI calls it. |
| ChatOps | Another integration to secure; results harder to tie to a commit. |

## 3. How the design serves the two graded aspects

**Developer experience.** A team writes a ~20-line YAML file and a short workflow; no Kubernetes, Helm or JavaScript, since a default scenario calls the listed endpoints. Sizes S/M/L answer "how hard should I test?"; S (50 rps, 60 s) is quick enough for a PR. The procedure is fixed: validate → estimate → provision → deploy → smoke → ramp/hold/ramp-down → report → teardown. Developers cannot skip the smoke test or forget teardown. All config errors are listed at once (exit 2) before anything is deployed. Results arrive as job summary, PR comment and artifact; failed thresholds fail the job, so it can be a required check. Peak CPU and memory next to the limits answer "my code or my limits?".

**Platform guardrails.** Each worry has an automatic control rather than a ticket review:

- *Capacity:* in-cluster concurrency cap, per-namespace quota and LimitRange, and a 0→N spot node pool on GKE.
- *Sizing:* presets plus hard caps (500 iterations/s and 500 req/s, 900 s, 2 CPU, 2 GiB).
- *Cost:* expiry annotation on every namespace, an estimate before each run, spot nodes that scale to zero, team labels for chargeback, and budget alerts.
- *Prod isolation:* separate GCP project and VPC with no peering, default-deny egress, load targets that are paths rather than URLs, and keyless CI access limited to the dedicated load-test project. Other teams' services are stubs, so no test loads another team.
- *Teardown:* CLI `finally`, an `if: always()` workflow step, and the janitor for killed runs.
- *Security:* allowed registry, admission policy, Pod Security `restricted`, no real secrets.

README.md has the full table, kind vs Terraform only.

## 4. Trade-offs accepted

| Area | Trade-off |
|---|---|
| Blast radius | Tests share one cluster: a noisy test can slow a neighbour, a cluster fault stops all tests. Quotas, the cap and a tainted node pool limit this; a cluster per test removes it at far higher cost and wait. |
| Cost | Spot nodes can be reclaimed mid-test. Rare for 1–15 min runs and cheap to rerun; on-demand nodes cost several times more. |
| Realism | Real Postgres, but stubs with fixed latency that never slow down under load: the test shows the service's own limits, not the system's. |
| Fidelity | kind is not GKE: one node, no autoscaling, Calico instead of Dataplane V2, no spot reclaims. The GKE path is reasoned, not tested. |
| DX | Results only at the end, no live metrics. Simple, but long L runs give no early signal. |
| Security | Images are checked by registry, not signature. |
| Complexity | Caps and image names are repeated in Python, chart, janitor and Terraform; drift tests catch mismatches. |
| Cloud CI | The GKE API is limited to authorized CIDRs, so cloud runs need self-hosted runners with known IPs. |

## 5. Known limitations

- The capacity check is racy: two runs that start at the same moment can both see a free slot.
- The CLI has no SIGTERM handler. If it is killed and the workflow's `always()` teardown step cannot run (runner lost), the namespace stays until the janitor deletes it: at most duration + 15 min, plus up to 5 min for the CronJob.
- An unexpected Python exception exits 1 (looks like a threshold failure) instead of 3.
- The cost estimate is a simple model (vCPU × minutes × an illustrative spot price), not billing data.
- CI mode uses a fresh kind cluster per run, so it cannot show contention between tests; that is shown on the shared local cluster ([docs/demo/guardrails.md](docs/demo/guardrails.md)).
- On a 4-CPU laptop only one test fits, so the local demo runs with `MAX_CONCURRENT=1`.

## 6. Next, with another week

1. **Apply the GCP Terraform** (with the baseline module wired in) in a sandbox project; run the workflow from a self-hosted runner against GKE.
2. **Live metrics:** Prometheus + Grafana in the platform, with k6 writing to it and a dashboard link in the job summary.
3. **Baselines:** keep each run's summary per service and branch; flag p95 regressions against `main`.
4. **Make the cap atomic** (a Lease or a ConfigMap compare-and-swap), and handle SIGTERM so cancelled runs tear down at once.
5. **Opt-in real dependencies** with the owning team's approval, recorded in config and allowed by a NetworkPolicy.
6. **Signed images** (Binary Authorization or a cosign policy) instead of registry checks.
7. **Backstage plugin** that writes `loadtest.yaml` and shows past runs.
