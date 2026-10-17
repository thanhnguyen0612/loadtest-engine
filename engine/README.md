# Developer guide: load-testing your service

This is for service teams. You need no Kubernetes, Helm or Terraform knowledge. You add a `loadtest/` folder to your repo and a short workflow that calls the platform's reusable workflow. The sample service in `services/sample-api/` is a complete example.

## 1. What you add to your repo

```
<your-service>/
├── Dockerfile                       # must set a numeric non-root USER (see section 6)
├── .github/workflows/loadtest.yml   # build the image, then `uses:` the reusable workflow
└── loadtest/
    ├── loadtest.yaml                # required: what to test, how hard, what counts as a pass
    ├── seed.sql                     # optional: schema and data for the test database
    ├── stubs/<dependency>/*.json    # optional: WireMock mappings for each stubbed service
    └── scenario.js                  # optional: custom k6 scenario
```

Your workflow is a copy of [`sample-api-loadtest.yml`](../.github/workflows/sample-api-loadtest.yml) with your paths and service name.

## 2. What happens when you run it

The order is fixed. You cannot skip a step.

1. **Validate** `loadtest.yaml` against the schema and the platform caps. All problems are listed at once.
2. **Estimate** the cost and print the resolved plan.
3. **Wait for a slot** if the cluster is at its concurrency cap (checked every 15 s, gives up after 10 min).
4. **Provision** namespace `lt-<service>-<run-id>` with quota, limits, default-deny network policies, labels and an expiry time.
5. **Deploy** your service, Postgres (seeded) and WireMock stubs. Waits up to 5 min for all of them to be ready.
6. **Smoke test**: 5 rps for 15 s over your endpoints. If 5 % or more of the requests fail, the run stops here as FAIL.
7. **Load test**: ramp up for 20 % of the duration, hold for 60 %, ramp down for 20 %.
8. **Report**: summary in the job, PR comment, downloadable artifact, job pass/fail.
9. **Teardown**: the namespace is deleted whatever happened. If the runner dies, a janitor deletes it after its expiry time.

## 3. `loadtest.yaml` reference

Unknown keys are rejected. File paths are relative to the folder that contains `loadtest.yaml` and must stay inside it.

```yaml
service:
  name: sample-api
  team: orders
load:
  size: S
  endpoints:
    - "GET /items"
    - method: POST
      path: /orders
      body: {item_id: 1, quantity: 2}
thresholds:
  p95_ms: 300
  error_rate: 0.01
dependencies:
  database:
    engine: postgres
    seed: seed.sql
  services:
    - name: payments-api
      mode: stub
      latency_ms: 80
      stubs: stubs/payments-api
```

| Field | Required | Default | Rules / cap |
|---|---|---|---|
| `service.name` | yes | — | DNS label: lowercase `a-z`, `0-9`, `-`, max 63 chars. Also the Kubernetes Service name. |
| `service.team` | yes | — | DNS label. Stamped on the namespace for chargeback. |
| `service.image` | yes, unless `--image` is passed | — | Must start with an allowed registry (`localhost:5001/` in the prototype). CI passes `--image`, so you normally leave it out. |
| `service.port` | no | `8080` | Port your container listens on. |
| `service.health_path` | no | `/healthz` | Readiness probe path. Must return 2xx once the service can take traffic. |
| `service.resources.cpu` | no | `250m` | Max `2000m`. Copy your prod value. Requests equal limits. |
| `service.resources.memory` | no | `256Mi` | Max `2048Mi`. `Mi` or `Gi`. |
| `load.size` | no | `S` | `S`, `M`, `L`, or `null` (then set both fields below). |
| `load.target_rps` | no | from size | 1–500. Overrides the preset's rps. |
| `load.duration_s` | no | from size | 1–900. Overrides the preset's duration. |
| `load.endpoints` | yes | — | Non-empty. `"METHOD /path"` or `{method, path, body}`. Methods: GET, POST, PUT, DELETE. Paths must start with `/`; full URLs are rejected. |
| `load.scenario` | no | — | Path to a custom k6 script (section 8). |
| `thresholds.p95_ms` | no | `500` | The test fails if p95 latency is at or above this. |
| `thresholds.error_rate` | no | `0.01` | The test fails if the failed-request rate is at or above this (status ≥ 400 or no response). |
| `dependencies.database.engine` | if `database` is set | — | Only `postgres`. |
| `dependencies.database.seed` | no | — | SQL file (section 7). |
| `dependencies.services[].name` | yes | — | DNS label. Also the stub's hostname. |
| `dependencies.services[].mode` | yes | — | Only `stub`. Real services are not reachable from a test. |
| `dependencies.services[].latency_ms` | no | `0` | 0–5000. Fixed delay added to every stub response. |
| `dependencies.services[].stubs` | no | `stubs/<name>` | Directory of WireMock mapping files. |

Check a config without a cluster:

```bash
.venv/bin/loadtest validate loadtest/loadtest.yaml --image localhost:5001/my-service:dev
.venv/bin/loadtest estimate loadtest/loadtest.yaml --image localhost:5001/my-service:dev
```

## 4. Sizes

| Size | Target rps | Duration | When to use |
|---|---|---|---|
| `S` (default) | 50 | 60 s | Every PR; checks that nothing regressed badly. |
| `M` | 150 | 300 s | Before a release or a change on a hot path. |
| `L` | 500 | 900 s | Capacity checks before an expected traffic peak. |

Start with `S`. Move up when `S` passes and you need an answer about higher load. You can change one dimension and keep the other, for example `size: S` with `duration_s: 120`. Because of the ramps, the average throughput is about 80 % of the target. The platform caps (500 rps, 900 s, 2000m CPU, 2048Mi memory) apply to every value.

The report shows your service's peak CPU and memory next to its limits. If the peak is near the limit, the latency numbers are about throttling, not your code.

## 5. Results and exit codes

| Where | What |
|---|---|
| GitHub job summary | PASS/FAIL headline; p50/p95/p99 latency, throughput, error rate; each threshold ✅/❌; peak CPU and memory vs limits; plan and cost estimate |
| PR comment | Same summary (only when the run was started from a labelled PR) |
| Artifact `loadtest-results` | `summary.md`, `report.html`, `k6-summary.json` (raw k6 summary) |
| Locally | The same three files under `results/<RUN_ID>/` |

Results appear when the run ends. There is no live dashboard yet.

| Exit code | Meaning | Typical cause |
|---|---|---|
| `0` | PASS | All thresholds met |
| `1` | FAIL | A threshold was missed, or the smoke test failed |
| `2` | Config invalid | Schema error, cap exceeded, wrong registry, missing seed/stubs file. Nothing was deployed. |
| `3` | Platform error | No free slot within 10 min, service never became ready, k6 produced no results, or an unexpected platform error (the one-line error is printed and written to the report; `LOADTEST_DEBUG=1` adds the traceback). The report names the failed step and lists pod states. |

## 6. Image requirements

- The image must be in the allowed registry. Anything else is rejected by the validator and again by the cluster's admission policy.
- **The image must run as a numeric, non-root user**, for example `USER 10001` in the Dockerfile. Test namespaces enforce Pod Security `restricted` (`runAsNonRoot: true`). Kubernetes cannot check that a named user such as `USER app` is non-root, so it refuses to start the container. The run then fails at deploy with exit 3 and a pod in `CreateContainerConfigError`.
- The service gets these environment variables:
  - `DATABASE_URL=postgresql://loadtest:loadtest@postgres:5432/loadtest` when a database is configured. This password is not a secret; the database exists only for this test.
  - `<NAME>_URL=http://<name>:8080` for each stubbed service, with the name upper-cased and `-` turned into `_` (for example `PAYMENTS_API_URL`).
- The service can reach only pods in its own namespace and DNS. Any other outbound call times out.
- Do not put real secrets in the image or in the `loadtest/` folder.

## 7. Seed data (`seed.sql`)

Postgres 16 runs the file once, when it first starts and before it accepts connections. It runs as user `loadtest` on database `loadtest`. Create your tables and insert enough rows to make queries realistic; [`services/sample-api/loadtest/seed.sql`](../services/sample-api/loadtest/seed.sql) inserts 1,000 items with `generate_series`. The data lives in an `emptyDir` volume and is deleted with the namespace. Postgres has 500m CPU and 512Mi memory.

## 8. Stubs for other teams' services

Each entry under `dependencies.services` becomes a WireMock server named after it, on port 8080. Put WireMock [mapping files](https://wiremock.org/docs/stubbing/) in `loadtest/stubs/<name>/` (or the directory in `stubs`). All `*.json` files in that directory are loaded:

```
loadtest/stubs/payments-api/charge.json
```

```json
{
  "request": {"method": "POST", "urlPath": "/charge"},
  "response": {"status": 200, "jsonBody": {"status": "approved"}}
}
```

`latency_ms` is added to every mapping as `fixedDelayMilliseconds`, unless the mapping already sets `fixedDelayMilliseconds` (other WireMock delay types are not detected, so the two would add up).

If you leave out `stubs` and the default `stubs/<name>/` directory is absent or has no `*.json` files, the stub answers every request with `200 {"ok": true}`. If you set `stubs` to a directory that does not exist, validation fails (exit 2).

## 9. Custom scenario (`scenario.js`)

Most services do not need one. The default scenario sends requests to your `endpoints` in turn. Write a custom scenario when a request depends on an earlier response (for example, list items and then order one of them).

The contract:

- The file is an ES module whose **default export is a function** that performs one iteration.
- The platform's `runner.js` imports it and owns everything else: the arrival rate, the stages, the thresholds and the summary. `options` or `handleSummary` exported by your file are ignored, so you cannot change the load shape.
- Available environment variables: `BASE_URL` (for example `http://sample-api:8080`), `ENDPOINTS` (your endpoints as JSON) and `PHASE` (`smoke` or `load`). Your scenario also runs in the smoke phase.
- The rate is in **iterations** per second. The default scenario sends one request per iteration, so rps equals requests per second. A custom iteration with two requests sends twice the target rps (the example below reported 80 req/s at size S).
- **Request-rate guard:** in the smoke and load phases the runner also has a threshold `http_reqs rate<=500` (the platform cap) with `abortOnFail`. If your scenario's average request rate goes above 500 req/s, k6 aborts the run (checked after the first 10 s) and the report shows a failed `request rate (platform cap)` check, exit 1.
- You can import k6 modules (`k6/http`, `k6`, ...) and `./endpoints.js`. Other local files are not shipped.
- Thresholds use `http_req_duration` and `http_req_failed`, so a response with status ≥ 400 counts as an error. `check()` results appear in the raw summary but do not decide pass/fail.

```js
import http from 'k6/http';

const BASE = __ENV.BASE_URL;

export default function () {
  // Order the first item the list returns.
  const items = http.get(`${BASE}/items?limit=5`).json();
  http.post(`${BASE}/orders`, JSON.stringify({ item_id: items[0].id, quantity: 1 }),
    { headers: { 'Content-Type': 'application/json' } });
}
```

## 10. For platform maintainers

- Caps, presets, component sizes and approved images are defined once in `cli/src/loadtest/limits.py`. The chart (`chart/loadtest-env/values.yaml`), the janitor and the Terraform baseline repeat some of them; `cli/tests/test_chart.py`, `janitor/test_janitor.py` and `cli/tests/test_infra_sync.py` fail if they drift.
- `LOADTEST_MAX_CONCURRENT_TESTS` overrides the concurrency cap. `LOADTEST_CHART_DIR` and `LOADTEST_K6_DIR` point the CLI at the chart and k6 scripts when it is not run from a repo checkout.
- On GKE, pass `--scheduling chart/loadtest-env/values-gke.example.yaml` to `loadtest run` so every pod lands on the tainted spot node pool.
- Tests: `.venv/bin/pytest engine -q` and `.venv/bin/ruff check engine` (or `make test` from the repo root).
