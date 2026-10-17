# Self-Service Ephemeral Load Testing Engine — Design

Date: 2026-10-17
Source brief: "Sr. DevOps Engineer (Sep 2026) - Take-home Assignment" (PDF, kept outside this repo at `../Senior DevOps Engineer - Vietnam - Sep 2026 - Take-home Assignment.pdf`).

## 1. Intent and why

A mid-sized company (~50 microservices on a major cloud) load-tests services through a ticket loop: dev files ticket → DevOps provisions env, deploys service, stands up load generators → dev runs the test and reads metrics → dev files another ticket for teardown. It is slow, burns DevOps time on repetitive work, and gets skipped under deadline pressure.

We build a **self-service load testing engine**: a developer adds a small config file to their service repo and triggers a GitHub Actions workflow; the platform creates an isolated ephemeral environment, deploys the service and its dependencies, runs a standard load-test procedure, reports results back in GitHub, and always tears down — with DevOps guardrails enforced automatically rather than by ticket review.

The deliverable is a take-home submission. Graders explicitly value **reasoning and structure over polish and feature count**, and "a small end-to-end prototype beats a large half-built one". Every scope decision should favour a working end-to-end path over breadth.

## 2. Deliverables (from the brief)

Single repo (this one, `loadtest-engine`) containing:

1. Working prototype, runnable end-to-end for at least one sample service.
2. IaC modules structured for real reuse.
3. A CI/CD pipeline.
4. A demo video (recorded by the candidate; the repo should make the demo easy to run).
5. `README.md` — how to run locally and (conceptually) in the cloud.
6. `WRITEUP.md` — 2–3 pages max: assumed context, alternatives considered (env provisioning, load generator, IaC layout, developer interface), trade-offs (cost, complexity, blast radius, DX, security), what's next with another week.
7. `AI-USAGE.md` — tools used, representative prompts, agentic workflow/config, how AI output was validated. This design doc and the plan that follows are evidence for it.

Time-box: 7 calendar days.

## 3. Goals, non-goals, anti-goals

**Goals**
- Developer runs a load test with one config file and one trigger; no ticket, no manual teardown.
- A sensible, non-skippable procedure: validate → provision → deploy → smoke → ramp/hold/ramp-down load → results → teardown.
- Guardrails enforced by the platform, not by trust: capacity, sizing, cost, prod isolation, forced teardown, security baseline.
- Runs fully end-to-end, for free, on a laptop and in GitHub Actions.
- GCP Terraform modules that look like what would be shipped for real.

**Non-goals (for this prototype)**
- Live metrics dashboard (Grafana/Prometheus) — listed as "next".
- Run-over-run regression comparison / baselines — "next".
- Real (non-stubbed) cross-team dependencies — "next", opt-in with owner approval.
- A web UI or portal.
- Actually applying the GCP Terraform (candidate has no GCP account).
- Supporting many languages/frameworks for services; one Python sample is enough.

**Anti-goals (would be a failure even if it "works")**
- Any path by which a test can reach production or production credentials.
- An environment that can outlive its test (leak) with no automatic cleanup.
- Requiring developers to understand Kubernetes/Helm/Terraform to run a test.
- Load generator results skewed by running on the GitHub runner instead of in-cluster.
- Over-built scope that leaves the end-to-end path broken or undemoable.

## 4. Key constraints and assumptions

- **No cloud account.** Cloud chosen: **GCP**. GCP Terraform is written and validated in CI (`fmt`, `validate`, lint, security scan) but never applied. GCP has no full local emulator, so the runnable prototype uses a **local kind cluster** standing in for the shared GKE load-test cluster. The writeup states this explicitly; on GKE only the cluster underneath changes.
- Candidate knows Terraform and has a GitHub account. Glue and sample service are **Python**.
- Assumed company context (to be stated in WRITEUP): services are containerised and already run on Kubernetes (GKE) in prod; each team has its own GitHub repo; images live in a company registry; teams own their service's DB schema.

## 5. Decisions (as agreed with the candidate)

| Area | Decision |
|---|---|
| Cloud | GCP (Terraform modules, not applied) |
| Runnable env | kind cluster locally and inside GitHub Actions |
| Ephemeral env model | Namespace-per-test on a shared, dedicated load-test cluster/node pool |
| Load generator | k6, running **in-cluster** as a Job inside the test namespace |
| IaC | Terraform for the long-lived platform; **Helm chart per test** for the ephemeral environment |
| CI/CD | GitHub Actions; DevOps-owned **reusable workflow** called by service repos |
| Developer interface | `loadtest/loadtest.yaml` in the service repo + trigger via `workflow_dispatch` (and/or PR) |
| Dependencies | Own DB: real ephemeral Postgres seeded from team's SQL. Other teams' services: WireMock stubs with configurable latency. Real shared deps: not allowed |
| Results | GitHub job summary + PR comment (when PR-triggered) + downloadable artifact (k6 HTML report + raw JSON) + job pass/fail from thresholds |
| Glue language | Python |
| Sample service | Python |

## 6. Developer experience

### 6.1 What the developer owns (in their service repo)

```
<service-repo>/
├── .github/workflows/loadtest.yml   # ~10 lines: `uses:` the DevOps reusable workflow
└── loadtest/
    ├── loadtest.yaml                # intent: what to test, how hard, what counts as pass
    ├── seed.sql                     # optional: seed data for the ephemeral DB
    └── scenario.js                  # OPTIONAL custom k6 script
```

In this submission there is one repo, so the sample service lives at `services/sample-api/` with its own `loadtest/` folder, standing in for a team repo. The README/WRITEUP explain that in reality this lives in each team's repo and calls the reusable workflow by reference.

### 6.2 `loadtest.yaml` (illustrative shape; exact schema is the builder's call within these semantics)

```yaml
service:
  image: <registry>/sample-api:<tag>
  port: 8080
  # resources default to the service's prod requests/limits; override allowed within caps
load:
  size: S | M | L          # t-shirt presets
  # or custom: target_rps, duration — bounded by hard maxima
  endpoints:                # used by the default scenario when no scenario.js
    - GET /items
    - POST /orders
thresholds:
  p95_ms: 300
  error_rate: 0.01
dependencies:
  database: { engine: postgres, seed: loadtest/seed.sql }
  services:
    - name: payments-api
      mode: stub
      latency_ms: 80
```

- **Default scenario**: platform ships a k6 script that exercises the `endpoints` list with the chosen load profile. Developers need no JavaScript. A custom `scenario.js` is optional for complex flows.
- **T-shirt sizes**: S/M/L map to (rps, duration) presets, e.g. S ≈ 50 rps × 2 min, L ≈ 500 rps × 15 min. Exact numbers are the builder's call but must fit inside the platform caps and be runnable on kind in CI (S must finish quickly in GitHub Actions).

### 6.3 Flow (enforced order, developer cannot skip steps)

1. **Validate** `loadtest.yaml` against schema and platform limits (max rps, max duration, max resources, allowed registry, target must be in-namespace). Reject with a clear message.
2. **Estimate & print** cost estimate (simple model) and the resolved plan.
3. **Provision** namespace via Helm: ResourceQuota, LimitRange, NetworkPolicies, labels (team, service, run id, expiry).
4. **Deploy** service, Postgres (seeded), WireMock stubs.
5. **Smoke test** — a short low-rate k6 run; fail fast if the service is not healthy.
6. **Load test** — k6 Job in-namespace: ramp up → hold → ramp down, with thresholds.
7. **Collect** k6 summary/JSON/HTML report and service peak CPU/memory.
8. **Report** to GitHub (see 6.4).
9. **Teardown** — always (`if: always()`), even on failure or cancel.

Concurrency (my call, see §13): the real cap is enforced **in the cluster** — the validate step counts active load-test namespaces and refuses/waits (bounded retry) when the cap is reached. A GitHub Actions concurrency group alone cannot queue more than one pending run, so it is only a per-service "one test at a time" convenience.

### 6.4 Results

- **Job summary**: PASS/FAIL headline; p50/p95/p99 latency; rps; error rate; each threshold ✅/❌; service peak CPU and memory vs limits.
- **PR comment** with the same summary when triggered from a PR.
- **Artifact**: k6 HTML report + raw JSON.
- **Exit status**: threshold breach fails the job, so teams can make it a required check.
- Writeup acknowledges the current-process step "developer watches metrics live" is not served by the prototype; live Grafana is in "next".

## 7. Platform guardrails

| Concern | Control | Prototype (kind) | GCP Terraform only |
|---|---|---|---|
| Capacity | Dedicated load-test node pool, autoscale 0→max | — | ✅ |
| Capacity | Per-namespace ResourceQuota + LimitRange defaults | ✅ | |
| Capacity | Max concurrent tests (in-cluster count of active test namespaces; per-service Actions concurrency group) | ✅ (visible in local persistent-cluster mode) | |
| Sizing | Default to prod-like resources; S/M/L presets; hard caps on custom values | ✅ | |
| Sizing | Peak CPU/mem shown in results | ✅ | |
| Cost | Max duration + expiry label on every namespace | ✅ | |
| Cost | Spot VMs, scale-to-zero node pool | — | ✅ |
| Cost | Printed cost estimate before run | ✅ | |
| Cost | GCP budget alerts; team/service labels for chargeback | labels ✅ | budget ✅ |
| Prod isolation | Separate GCP project, no network path/peering to prod, no prod creds | — | ✅ |
| Prod isolation | Default-deny NetworkPolicy (ingress+egress) except within namespace (+DNS) | ✅ (requires Calico on kind) | |
| Prod isolation | Target URL validated to be in-namespace | ✅ | |
| Prod isolation | GitHub → GCP via Workload Identity Federation (keyless), scoped to load-test cluster only | — | ✅ |
| Teardown | Always-run teardown step | ✅ | |
| Teardown | In-cluster janitor CronJob deletes expired namespaces (covers crashed/cancelled workflows) | ✅ | |
| Security | Allowed image registry only | ✅ (validator; admission policy is builder's call) | |
| Security | Pod Security Standard `restricted` on test namespaces; non-root | ✅ | |
| Security | No real secrets in tests | ✅ (validator/doc) | |

## 8. Dependencies

- **Own database**: ephemeral Postgres in the test namespace, seeded from the team's `seed.sql`, destroyed at teardown. Rationale: DB is frequently the real bottleneck; stubbing it hides what load tests should find.
- **Other teams' services**: WireMock stubs with configurable latency. Rationale: calling real services would load-test another team without consent — the shared-environment risk guardrails exist to prevent.
- **Real/shared deps**: blocked by NetworkPolicy; "opt-in with owner approval" is future work.

## 9. Sample service

Tiny Python HTTP API (framework is the builder's call) with:
- one Postgres table (read and write endpoints),
- one call to a downstream "payments-api" that is stubbed by WireMock in tests,
- a health endpoint used by the smoke test,
- a Dockerfile; image built in CI and loaded into kind.

## 10. Repository shape (indicative; builder may refine)

```
loadtest-engine/
├── README.md  WRITEUP.md  AI-USAGE.md
├── services/sample-api/             # stands in for a team repo (incl. loadtest/)
├── platform/
│   ├── cli/                         # Python glue: validate, render values, estimate, summarise
│   ├── chart/                       # Helm chart for one ephemeral test env
│   └── k6/                          # default scenario
├── infra/
│   ├── modules/                     # reusable Terraform modules (GCP: project, network, gke, nodepool, budget, wif; k8s: cluster-baseline/janitor/policies)
│   └── envs/                        # root configs: gcp-loadtest (not applied), local-kind (applied)
├── .github/workflows/
│   ├── loadtest-reusable.yml        # DevOps-owned reusable workflow
│   ├── sample-api-loadtest.yml      # the "team" caller
│   └── ci.yml                       # lint/test Python, helm lint, terraform fmt/validate/tflint/scan
└── docs/superpowers/specs/
```

## 11. CI/CD

- **ci.yml** (on PR/push): Python lint + unit tests (validator, renderer, summariser); `helm lint`/template; Terraform `fmt -check`, `validate`, tflint, security scan (e.g. checkov/trivy) for all modules incl. GCP.
- **Load test workflow** (dispatch / PR): creates kind cluster, installs Calico and platform baseline (Terraform `local-kind` env), builds sample image, runs the flow in §6.3, publishes results.
- The end-to-end Actions run is itself a demo reviewers can watch.

## 12. Left to the builder

- Exact `loadtest.yaml` schema and validation library; preset numbers; caps.
- Python web framework for the sample service; CLI framework.
- How results JSON is turned into markdown; how peak CPU/mem is collected (e.g. metrics-server).
- Whether allowed-registry/PSS is enforced by admission policy (e.g. Kyverno/ValidatingAdmissionPolicy) or only by the validator + PSS labels.
- Cost-estimate model (simple, documented).
- Local entrypoint UX (e.g. `make` targets) for running the same flow on a laptop.

## 13. Calls made after the builder check (Claude's calls, not the candidate's — flagged for review)

1. **Two run modes.** *CI mode*: each GitHub Actions run creates a throwaway kind cluster (proves the end-to-end flow). *Local shared mode*: a long-lived kind cluster on the laptop with the platform baseline installed, where several tests can run side by side — this is where quotas, the concurrency cap and the janitor are demonstrated (and recorded for the demo video). The writeup states that CI mode cannot show shared-cluster contention.
2. **Concurrency cap** enforced in-cluster by counting active test namespaces (label selector), not by Actions concurrency groups (which only hold one pending run).
3. **Image contract.** The caller builds and pushes its image and passes an image reference; the reusable workflow never builds team code. The prototype runs a **local registry next to kind** (the standard kind local-registry pattern) and that registry is the "allowed registry", so the registry check is real and testable end to end.
4. **Prod resource defaults.** `loadtest.yaml` has an optional `service.resources`; teams are expected to copy their prod requests/limits there. If omitted, platform defaults apply. The platform never queries prod. Results compare peak usage to whatever limits were used.
5. **Triggers.** `workflow_dispatch` and same-repo PRs (opt-in via a `loadtest` label). **Fork PRs are out of scope**, assuming internal company repos without forks; stated as an assumption in the writeup, since PR code (image, seed.sql, scenario.js) is untrusted input.
6. **Stub responses.** Teams provide WireMock mapping files under `loadtest/stubs/<name>/`; the platform supplies a generic 200 JSON default if none are given. `latency_ms` is applied as a fixed delay.
7. **Service wiring.** The platform injects `DATABASE_URL` and `<NAME>_URL` (e.g. `PAYMENTS_API_URL`) env vars into the service; the sample service reads them.

## 14. Writeup themes to carry

- Assumed context and the no-cloud-account assumption.
- Alternatives: env provisioning (namespace-per-test vs cluster-per-test vs VMs/serverless); load generator (k6 vs Locust vs JMeter vs Gatling); IaC layout (Terraform-everything vs Terraform platform + Helm per test); developer interface (config+Actions vs portal/Backstage vs CLI vs ChatOps).
- Trade-offs: shared cluster blast radius vs cost/speed; stubs vs realism; kind vs real GKE fidelity.
- Next week: live Grafana, baselines/regression detection, opt-in real deps, admission policies, Backstage plugin, real GKE apply.
