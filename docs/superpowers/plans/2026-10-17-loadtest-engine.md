# Self-Service Ephemeral Load Testing Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A developer drops `loadtest/loadtest.yaml` into a service repo, triggers a GitHub Actions workflow, and gets a PASS/FAIL load-test report while the platform provisions an isolated namespace, runs k6 in-cluster, enforces guardrails, and always tears down.

**Architecture:** A Python CLI (`loadtest`) validates the config against platform limits, renders Helm values, creates a labelled namespace, installs a Helm chart (quota, limits, network policies, service, seeded Postgres, WireMock stubs, k6 scripts), runs k6 smoke then load Jobs, samples peak CPU/memory, renders reports, and deletes the namespace. Terraform owns long-lived platform pieces: on kind, a baseline module (janitor CronJob + admission policy); on GCP, project-level modules that are validated but never applied. GitHub Actions provides a DevOps-owned reusable workflow plus a sample "team" caller.

**Tech Stack:** Python 3.12 (pydantic v2, PyYAML, pytest, ruff; FastAPI + psycopg 3 + httpx for the sample service), k6, Helm 3, kind + Calico + metrics-server + local registry, Terraform (kubernetes provider for local, google provider for GCP), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-17-loadtest-engine-design.md` (read it first; §13 lists post-review decisions).

## Global Constraints

- Repo root: `/Users/thanhnguyen/Source/Interview/loadtest-engine`. Platform code lives under `engine/` (not `platform/`, to avoid shadowing Python's stdlib `platform` module — deviation from spec §10, intentional).
- Python 3.12; CLI package `loadtest` at `engine/cli/src/loadtest/`, installed editable with `pip install -e engine/cli[dev]`; console script `loadtest`.
- Allowed registry in the prototype: `localhost:5001` (kind local-registry pattern). Platform-approved third-party images (pinned, exact strings shared by chart, k6 job and admission policy, defined once in `engine/cli/src/loadtest/limits.py` and mirrored in Terraform): `docker.io/library/postgres:16-alpine`, `docker.io/wiremock/wiremock:3.9.1`, `docker.io/grafana/k6:0.54.0`. Before first use, verify each tag exists with `docker manifest inspect <image>`; if one does not, pick the nearest existing tag and update every place it appears.
- Platform caps (`limits.py`): `MAX_RPS = 500`, `MAX_DURATION_S = 900`, `MAX_CPU_M = 2000`, `MAX_MEMORY_MI = 2048`, `MAX_CONCURRENT_TESTS = 3`, `TTL_BUFFER_S = 900`, `MAX_LATENCY_MS = 5000`.
- Presets: `S = (50 rps, 60 s)`, `M = (150 rps, 300 s)`, `L = (500 rps, 900 s)`. Default size `S`.
- Default service resources when omitted: `cpu 250m, memory 256Mi`. Default thresholds: `p95_ms 500, error_rate 0.01`.
- Labels/annotations on every test namespace: `loadtest.platform/managed=true`, `loadtest.platform/team`, `loadtest.platform/service`, `loadtest.platform/run-id`; annotation `loadtest.platform/expires-at` = unix epoch seconds; PSS labels `pod-security.kubernetes.io/enforce=restricted` and `.../enforce-version=latest`.
- Every pod the platform creates: `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile.type: RuntimeDefault`, explicit resource requests and limits.
- Config file paths (`seed`, `stubs`, `scenario`) are relative to the directory containing `loadtest.yaml`.
- No real secrets anywhere; Postgres password is the literal `loadtest` in a plain env var and is documented as non-secret.
- CLI exit codes: `0` pass, `1` thresholds failed (incl. smoke failure), `2` config invalid, `3` platform/infra error (capacity timeout, deploy timeout, missing results).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Workflow cancelled or runner killed mid-test** → namespace must still disappear once `expires-at` passes (janitor). Test: Task 8 `test_expired_namespaces_selects_only_past_expiry_and_managed`.
2. **k6 pod dies without printing a summary (OOMKilled, deadline)** → report says "no results collected", CLI exits 3, teardown still runs; no stack trace. Test: Task 4 `test_extract_summary_missing_markers_raises` + Task 9 `test_run_missing_summary_exits_3_and_tears_down`.
3. **Service never becomes ready (bad image, crash loop)** → `helm install --wait` times out, CLI prints which step failed, exits 3, tears down. Test: Task 9 `test_run_helm_failure_exits_3_and_tears_down`.
4. **Config with several problems at once (absolute URL endpoint, wrong registry, rps over cap)** → all errors listed together, not one at a time. Test: Task 1 `test_validate_reports_all_errors_together`.
5. **Two runs of the same service at once / very long service names** → distinct, valid (≤63 chars, DNS-1123) namespace names. Test: Task 2 `test_namespace_name_unique_per_run_and_truncated`.

---

### Task 1: CLI scaffold, platform limits, and config schema/validation

**Files:**
- Create: `.gitignore`, `engine/cli/pyproject.toml`, `engine/cli/src/loadtest/__init__.py`, `engine/cli/src/loadtest/limits.py`, `engine/cli/src/loadtest/config.py`
- Test: `engine/cli/tests/test_config.py`, `engine/cli/tests/fixtures/valid/loadtest.yaml`, `engine/cli/tests/fixtures/valid/seed.sql`

**Interfaces:**
- Produces:
  - `limits.py`: constants from Global Constraints plus `ALLOWED_REGISTRIES: tuple[str, ...] = ("localhost:5001",)`, `PLATFORM_IMAGES: dict[str, str]` with keys `postgres`, `wiremock`, `k6`, and `PRESETS: dict[str, tuple[int, int]]`.
  - `config.py`: pydantic models (all `extra="forbid"`): `Resources(cpu: str = "250m", memory: str = "256Mi")`, `ServiceSpec(name, team, image: str | None = None, port: int = 8080, health_path: str = "/healthz", resources: Resources)`, `Endpoint(method: Literal["GET","POST","PUT","DELETE"], path: str, body: dict | None = None)`, `LoadSpec(size: Literal["S","M","L"] | None = "S", target_rps: int | None = None, duration_s: int | None = None, endpoints: list[Endpoint], scenario: str | None = None)`, `Thresholds(p95_ms: int = 500, error_rate: float = 0.01)`, `DatabaseDep(engine: Literal["postgres"], seed: str | None = None)`, `ServiceDep(name: str, mode: Literal["stub"], latency_ms: int = 0, stubs: str | None = None)`, `Dependencies(database: DatabaseDep | None = None, services: list[ServiceDep] = [])`, `LoadTestConfig(service, load, thresholds, dependencies, base_dir: Path)`.
  - `class ConfigError(Exception)` with attribute `errors: list[str]`.
  - `parse_cpu_m(s: str) -> int` ("250m"→250, "1"→1000), `parse_memory_mi(s: str) -> int` ("256Mi"→256, "1Gi"→1024).
  - `load_config(path: Path, image_override: str | None = None) -> LoadTestConfig` — reads YAML, applies override, validates, raises `ConfigError` listing **every** problem.

- [ ] **Step 1: Scaffold.** `pyproject.toml` (setuptools, src layout, deps `pydantic>=2.7`, `pyyaml>=6`; extra `dev` = `pytest`, `ruff`; console script `loadtest = "loadtest.__main__:main"`; ruff line-length 100). `.gitignore` covers `.venv/`, `__pycache__/`, `.terraform/`, `*.tfstate*`, `results/`. Create venv: `python3.12 -m venv .venv && .venv/bin/pip install -e 'engine/cli[dev]'`.
- [ ] **Step 2: Write failing tests** in `test_config.py`:

```python
def test_load_valid_fixture():
    cfg = load_config(FIXTURES / "valid" / "loadtest.yaml")
    assert cfg.service.name == "sample-api" and cfg.service.team == "orders"
    assert cfg.load.endpoints[0] == Endpoint(method="GET", path="/items")
    assert cfg.dependencies.database.seed == "seed.sql"

def test_endpoint_string_form_parsed():  # "POST /orders" → Endpoint(method="POST", path="/orders")
def test_image_override_wins(): ...      # image_override="localhost:5001/x:1" → cfg.service.image
def test_missing_image_rejected(): ...   # no image and no override → ConfigError mentioning "image"
def test_validate_reports_all_errors_together(tmp_path):
    # endpoint "GET http://prod.internal/x", image "docker.io/evil/x:1", target_rps 10000
    with pytest.raises(ConfigError) as e: load_config(p)
    msgs = " ".join(e.value.errors)
    assert "absolute" in msgs and "registry" in msgs and "500" in msgs
    assert len(e.value.errors) >= 3
def test_duration_over_cap_rejected(): ...   # duration_s 3600 → error mentions "900"
def test_resources_over_cap_rejected(): ...  # cpu "4" → error mentions cpu cap
def test_unknown_field_rejected(): ...       # top-level "env: {...}" → ConfigError (blocks secrets injection)
def test_seed_file_must_exist(): ...         # seed: missing.sql → ConfigError
def test_latency_over_cap_rejected(): ...    # latency_ms 6000
def test_parse_cpu_and_memory(): assert parse_cpu_m("1") == 1000 and parse_memory_mi("1Gi") == 1024
```

Fixture `valid/loadtest.yaml`: service `sample-api`, team `orders`, image `localhost:5001/sample-api:test`, endpoints `["GET /items", {method: POST, path: /orders, body: {item_id: 1, quantity: 1}}]`, database postgres seed `seed.sql`, services `[{name: payments-api, mode: stub, latency_ms: 80}]`.

- [ ] **Step 3: Run** `.venv/bin/pytest engine/cli/tests/test_config.py -v` → FAIL (import errors).
- [ ] **Step 4: Implement** `limits.py` and `config.py`. Collect pydantic errors and custom checks (registry prefix, absolute URL in path, caps, file existence, `custom target_rps/duration_s` must both be set when `size` is null) into one `ConfigError`. Service and dependency names must be DNS-1123 labels.
- [ ] **Step 5: Run** same command → PASS; `.venv/bin/ruff check engine/cli` clean.
- [ ] **Step 6: Commit** `feat(cli): config schema and platform limits`.

### Task 2: Resolved plan, namespace naming, cost estimate

**Files:**
- Create: `engine/cli/src/loadtest/plan.py`, `engine/cli/src/loadtest/estimate.py`
- Test: `engine/cli/tests/test_plan.py`, `engine/cli/tests/test_estimate.py`

**Interfaces:**
- Consumes: `LoadTestConfig`, `PRESETS`, limits.
- Produces:
  - `@dataclass(frozen=True) Stage(duration_s: int, target_rps: int)`
  - `@dataclass(frozen=True) TestPlan(config: LoadTestConfig, run_id: str, namespace: str, target_rps: int, duration_s: int, stages: list[Stage], expires_at: int)`
  - `namespace_name(service: str, run_id: str) -> str`
  - `build_plan(config: LoadTestConfig, run_id: str, now: int) -> TestPlan`
  - `@dataclass CostEstimate(usd: float, vcpu: float, minutes: float)`; `estimate_cost(plan: TestPlan) -> CostEstimate`; `USD_PER_VCPU_HOUR = 0.012` (illustrative GKE spot rate, documented as such).

- [ ] **Step 1: Failing tests:**

```python
def test_stages_ramp_hold_rampdown():
    p = build_plan(cfg(size="S"), "123", now=1000)
    assert [s.target_rps for s in p.stages] == [50, 50, 0]
    assert [s.duration_s for s in p.stages] == [12, 36, 12]      # 20% / 60% / 20% of 60s
def test_custom_rps_duration_used(): ...                          # size None, 200 rps, 120 s
def test_expires_at_is_now_plus_duration_plus_buffer():
    assert build_plan(cfg(size="S"), "1", now=1000).expires_at == 1000 + 60 + 900
def test_namespace_name_unique_per_run_and_truncated():
    a, b = namespace_name("x" * 80, "111"), namespace_name("x" * 80, "222")
    assert a != b and len(a) <= 63 and re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", a)
    assert namespace_name("Sample_API", "42") == "lt-sample-api-42"
def test_estimate_positive_and_scales_with_duration(): ...       # L > S, usd > 0
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/cli/tests/test_plan.py engine/cli/tests/test_estimate.py -v` → FAIL.
- [ ] **Step 3: Implement.** Stages use integer rounding with hold absorbing the remainder. Namespace: `lt-{sanitized service}-{sanitized run_id}`, service part truncated so total ≤ 63, run_id kept whole (truncate run_id to last 12 chars first). Estimate: vCPU = service cpu + Postgres 0.5 + 0.25 per stub + k6 1.0; minutes = duration + 5 min overhead.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(cli): test plan, namespace naming, cost estimate`.

### Task 3: Helm values rendering (incl. stub latency injection)

**Files:**
- Create: `engine/cli/src/loadtest/values.py`
- Test: `engine/cli/tests/test_values.py`, `engine/cli/tests/fixtures/valid/stubs/payments-api/charge.json`

**Interfaces:**
- Consumes: `TestPlan`, `PLATFORM_IMAGES`, `parse_cpu_m`, `parse_memory_mi`.
- Produces:
  - `apply_latency(mapping: dict, latency_ms: int) -> dict` — returns copy; sets `response.fixedDelayMilliseconds` only if absent and latency > 0.
  - `DEFAULT_STUB_MAPPING: dict` — any method, `urlPattern: ".*"`, 200, JSON body `{"ok": true}`.
  - `render_values(plan: TestPlan, k6_dir: Path, scheduling: dict | None = None) -> dict` with exactly these top-level keys (the chart in Task 7 consumes them):
    `runId`, `images` (`postgres`, `wiremock`, `k6`), `service` (`name`, `image`, `port`, `healthPath`, `resources` {`cpu`,`memory`}, `env` dict), `database` (`enabled`, `seedSql`), `stubs` (list of `{name, mappings: {filename: json_string}}`), `k6` (`files`: `{filename: content}` — always `runner.js`, `endpoints.js`, `scenario.js`; `scenario.js` is the team's custom script if given, else `export { default } from './endpoints.js';`), `quota` (`cpu`, `memory`, `pods`), `scheduling` (`nodeSelector`, `tolerations`).
  - Service env contract: `DATABASE_URL=postgresql://loadtest:loadtest@postgres:5432/loadtest` when database enabled; `<NAME>_URL=http://<name>:8080` per stub with `NAME` upper-cased and `-`→`_` (e.g. `PAYMENTS_API_URL`).

- [ ] **Step 1: Failing tests:**

```python
def test_apply_latency_sets_delay_when_absent(): assert apply_latency({"response": {"status": 200}}, 80)["response"]["fixedDelayMilliseconds"] == 80
def test_apply_latency_keeps_explicit_delay(): ...
def test_env_contract():
    v = render_values(plan_from_fixture(), K6_DIR)
    assert v["service"]["env"]["PAYMENTS_API_URL"] == "http://payments-api:8080"
    assert v["service"]["env"]["DATABASE_URL"].startswith("postgresql://loadtest:loadtest@postgres:5432/")
def test_stub_mappings_loaded_with_latency(): ...   # charge.json present, delay 80 injected
def test_default_stub_mapping_when_none_provided(): ...
def test_quota_covers_all_components(): ...         # quota cpu ≥ service+postgres+stubs+k6 in millicores
def test_custom_scenario_included(): ...            # custom scenario.js copied into k6.files
def test_default_scenario_reexports_endpoints(): ... # no custom → scenario.js re-exports endpoints.js
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/cli/tests/test_values.py -v` → FAIL.
- [ ] **Step 3: Implement.** Stub dir defaults to `<base_dir>/stubs/<name>` if it exists; all `*.json` files there are mappings. Quota = sum of component limits × 1.2 rounded up, pods = 4 + number of stubs + 2 (k6 jobs). `k6_dir` is `engine/k6` (Task 6 writes those files; tests use a tmp dir with dummy files).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(cli): render helm values for a test environment`.

### Task 4: Results — k6 summary extraction, Result model, Markdown and HTML reports

**Files:**
- Create: `engine/cli/src/loadtest/report.py`
- Test: `engine/cli/tests/test_report.py`, `engine/cli/tests/fixtures/k6_summary_pass.json`, `engine/cli/tests/fixtures/k6_summary_fail.json`

**Interfaces:**
- Produces:
  - `SUMMARY_BEGIN = "===LOADTEST_SUMMARY_BEGIN==="`, `SUMMARY_END = "===LOADTEST_SUMMARY_END==="` (k6 runner.js in Task 6 prints the summary JSON between these).
  - `class MissingResults(Exception)`
  - `extract_summary(logs: str) -> dict` — raises `MissingResults` if markers absent or JSON invalid.
  - `@dataclass ThresholdCheck(name: str, limit: str, actual: str, ok: bool)`
  - `@dataclass Result(passed: bool, smoke_passed: bool, p50_ms: float, p95_ms: float, p99_ms: float, rps: float, error_rate: float, checks: list[ThresholdCheck], peak_cpu_m: int | None, peak_mem_mi: int | None, cpu_limit_m: int, mem_limit_mi: int)`
  - `build_result(summary: dict, plan: TestPlan, peak_cpu_m: int | None, peak_mem_mi: int | None, smoke_passed: bool = True) -> Result` — reads `metrics.http_req_duration.values` keys `med`, `p(95)`, `p(99)`; `metrics.http_reqs.values.rate`; `metrics.http_req_failed.values.rate`; pass = every `metrics.*.thresholds[*].ok` true.
  - `render_markdown(result: Result | None, plan: TestPlan, estimate: CostEstimate, error: str | None = None) -> str`
  - `render_html(result: Result | None, plan: TestPlan, estimate: CostEstimate, error: str | None = None) -> str` — self-contained page, no external assets.

- [ ] **Step 1: Write fixtures** shaped like k6 `handleSummary` data (`metrics.<name>.values`, `metrics.<name>.thresholds: {"p(95)<300": {"ok": true}}`). Fail fixture has `p(95)` 812 ms and its threshold `ok: false`.
- [ ] **Step 2: Failing tests:**

```python
def test_extract_summary_between_markers(): assert extract_summary(f"noise\n{SUMMARY_BEGIN}\n{{\"a\":1}}\n{SUMMARY_END}\n") == {"a": 1}
def test_extract_summary_missing_markers_raises(): pytest.raises(MissingResults, extract_summary, "k6 OOMKilled")
def test_build_result_pass(): r = build_result(PASS, plan, 180, 120); assert r.passed and r.p95_ms == pytest.approx(...)
def test_build_result_fail_marks_failed_check(): ...   # passed False, one check ok False, actual "812 ms"
def test_markdown_headline_and_table():
    md = render_markdown(build_result(FAIL, plan, 900, 200), plan, est)
    assert md.splitlines()[0].startswith("## ❌ FAIL") and "p95" in md and "| ❌ |" in md
    assert "Peak CPU" in md and "250m" in md          # peak vs limit shown
def test_markdown_error_variant(): assert "no results collected" in render_markdown(None, plan, est, error="no results collected")
def test_markdown_smoke_failed_variant(): ...          # smoke_passed False → headline mentions smoke
def test_html_contains_same_numbers(): ...
```

- [ ] **Step 3: Run** `.venv/bin/pytest engine/cli/tests/test_report.py -v` → FAIL.
- [ ] **Step 4: Implement.** Markdown layout: headline (`## ✅ PASS — sample-api` / `## ❌ FAIL — …` / `## ⚠️ ERROR — …`), one-line plan (size, rps, duration, namespace, est. cost), metrics table, thresholds table with ✅/❌, resources table (peak vs limit, "n/a" if None).
- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Commit** `feat(cli): parse k6 summary and render reports`.

### Task 5: Sample service (`sample-api`) and its team-owned loadtest folder

**Files:**
- Create: `services/sample-api/app/main.py`, `services/sample-api/requirements.txt`, `services/sample-api/requirements-dev.txt`, `services/sample-api/Dockerfile`, `services/sample-api/tests/test_app.py`, `services/sample-api/loadtest/loadtest.yaml`, `services/sample-api/loadtest/seed.sql`, `services/sample-api/loadtest/stubs/payments-api/charge.json`

**Interfaces:**
- Consumes: env contract from Task 3 (`DATABASE_URL`, `PAYMENTS_API_URL`); config schema from Task 1.
- Produces: HTTP API on port 8080 — `GET /healthz` → `200 {"status":"ok"}` (checks DB with `SELECT 1`); `GET /items?limit=N` (default 20, max 100) → `200 [{"id","name","price_cents"}]`; `POST /orders` body `{"item_id": int, "quantity": int}` → calls `POST {PAYMENTS_API_URL}/charge` with `{"amount_cents"}`, inserts into `orders`, returns `201 {"order_id", "status": "approved"}`; unknown item → 404; payments non-2xx → 502. Image `localhost:5001/sample-api:<tag>`.

- [ ] **Step 1: Failing tests** with FastAPI `TestClient` and dependency overrides (`get_repo`, `get_payments`) using in-memory fakes — no real DB:

```python
def test_healthz_ok(): assert client.get("/healthz").json() == {"status": "ok"}
def test_items_limit_capped(): assert len(client.get("/items?limit=1000").json()) <= 100
def test_create_order_charges_and_returns_201(): ...   # fake payments records amount = price*qty
def test_order_unknown_item_404(): ...
def test_order_payment_failure_502(): ...
```

- [ ] **Step 2: Run** `python3.12 -m venv services/sample-api/.venv && services/sample-api/.venv/bin/pip install -r services/sample-api/requirements-dev.txt && services/sample-api/.venv/bin/pytest services/sample-api/tests -v` → FAIL.
- [ ] **Step 3: Implement** with FastAPI, `psycopg_pool.ConnectionPool` (min 2, max 10, opened at startup), shared `httpx.Client` with 2 s timeout. Dockerfile: `python:3.12-slim`, non-root `USER 10001`, `uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 2`.
- [ ] **Step 4: Run tests** → PASS. `docker build -t sample-api:dev services/sample-api` succeeds.
- [ ] **Step 5: Loadtest folder.** `seed.sql`: tables `items(id serial pk, name text, price_cents int)` and `orders(id serial pk, item_id int, quantity int, created_at timestamptz default now())`, 1000 items via `generate_series`. `charge.json`: WireMock mapping `POST /charge` → 200 `{"status":"approved"}`. `loadtest.yaml`: as the Task 1 fixture but without `image` (passed by workflow), `size: S`, thresholds `p95_ms: 300`, `error_rate: 0.01`, POST body `{item_id: 1, quantity: 2}`. Verify: `.venv/bin/python -c "from loadtest.config import load_config; load_config(__import__('pathlib').Path('services/sample-api/loadtest/loadtest.yaml'), 'localhost:5001/sample-api:dev')"` exits 0.
- [ ] **Step 6: Commit** `feat(sample-api): sample service with loadtest config`.

### Task 6: k6 scripts and k6 Job manifest builder

**Files:**
- Create: `engine/k6/runner.js`, `engine/k6/endpoints.js`, `engine/cli/src/loadtest/k6job.py`
- Test: `engine/cli/tests/test_k6job.py`

**Interfaces:**
- Consumes: `TestPlan`, `PLATFORM_IMAGES["k6"]`, `SUMMARY_BEGIN/END`.
- Produces:
  - `runner.js` (platform-owned; developers never set options): reads env `BASE_URL`, `PHASE` (`smoke`|`load`), `STAGES` (JSON list of `{duration_s,target_rps}`), `P95_MS`, `ERROR_RATE`, `ENDPOINTS` (JSON). Smoke: `constant-arrival-rate` 5 rps for 15 s, threshold `http_req_failed: rate<0.05` only. Load: `ramping-arrival-rate`, `startRate: 1`, `timeUnit: '1s'`, `preAllocatedVUs: 50`, `maxVUs: 500`, thresholds `http_req_duration: p(95)<P95_MS`, `http_req_failed: rate<ERROR_RATE`. `summaryTrendStats: ['avg','med','p(95)','p(99)','max']`. Default export imports `./scenario.js` if present else `./endpoints.js` (decided at render time: `values.py` always writes a `scenario.js` key — copy of the custom script, or a one-line re-export of `endpoints.js`). `handleSummary` returns `{stdout: SUMMARY_BEGIN + "\n" + JSON.stringify(data) + "\n" + SUMMARY_END + "\n"}`.
  - `endpoints.js`: default function iterates `ENDPOINTS` round-robin by `__ITER`, sends with JSON body if present, `check` status < 400.
  - `k6_job(plan: TestPlan, phase: Literal["smoke","load"], scheduling: dict | None = None) -> dict` — Job `k6-<phase>`, `backoffLimit: 0`, `activeDeadlineSeconds` = 15+60 for smoke, `duration_s + 120` for load; mounts ConfigMap `k6-scripts` at `/scripts`; command `k6 run /scripts/runner.js`; env above with `BASE_URL=http://<service>:<port>`; resources req/limit `1000m`/`1Gi`; restricted securityContext; `nodeSelector`/`tolerations` from `scheduling`.

- [ ] **Step 1: Failing tests** (`test_k6job.py`):

```python
def test_k6_job_env_and_deadline():
    j = k6_job(plan, "load")
    env = {e["name"]: e["value"] for e in j["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["BASE_URL"] == "http://sample-api:8080" and env["PHASE"] == "load"
    assert json.loads(env["STAGES"]) == [{"duration_s": 12, "target_rps": 50}, {"duration_s": 36, "target_rps": 50}, {"duration_s": 12, "target_rps": 0}]
    assert j["spec"]["activeDeadlineSeconds"] == 60 + 120 and j["spec"]["backoffLimit"] == 0
def test_k6_job_security_context_restricted(): ...
def test_k6_job_image_is_platform_pinned(): ...
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/cli/tests/test_k6job.py -v` → FAIL. Implement → PASS.
- [ ] **Step 3: Smoke-check the scripts locally** against the sample service: `docker run --rm -i --network host -e BASE_URL=http://localhost:8080 -e PHASE=smoke -e ENDPOINTS='[{"method":"GET","path":"/healthz"}]' -v $PWD/engine/k6:/scripts grafana/k6:0.54.0 run /scripts/runner.js` (with a `scenario.js` re-export placed in a tmp copy) → output contains `===LOADTEST_SUMMARY_BEGIN===`. If the sample isn't running locally, start it with a throwaway Postgres container first.
- [ ] **Step 4: Commit** `feat(k6): platform runner script and job builder`.

### Task 7: Helm chart `loadtest-env`

**Files:**
- Create: `engine/chart/loadtest-env/Chart.yaml`, `values.yaml`, `templates/_helpers.tpl`, `templates/resourcequota.yaml`, `templates/limitrange.yaml`, `templates/networkpolicy.yaml`, `templates/service.yaml` (Deployment + Service for the app), `templates/postgres.yaml`, `templates/stubs.yaml`, `templates/k6-scripts.yaml`
- Test: `engine/cli/tests/test_chart.py` (skips if `helm` not on PATH)

**Interfaces:**
- Consumes: values shape from Task 3 exactly.
- Produces: in the release namespace — ResourceQuota `loadtest-quota`; LimitRange `loadtest-defaults` (default 250m/256Mi, max = platform caps); NetworkPolicies `default-deny-all` (ingress+egress), `allow-same-namespace` (ingress+egress to pods in this namespace), `allow-dns` (egress UDP/TCP 53 to `kube-system` pods `k8s-app=kube-dns`); Deployment+Service named `<service.name>` with readiness probe on `healthPath`; Deployment+Service `postgres` (port 5432, `POSTGRES_USER/PASSWORD/DB=loadtest`, `runAsUser: 70`, `fsGroup: 70`, emptyDir data at `/var/lib/postgresql/data` with `PGDATA` subdir, seed ConfigMap at `/docker-entrypoint-initdb.d`); per stub a ConfigMap `<name>-mappings` mounted at `/home/wiremock/mappings`, Deployment+Service `<name>` on port 8080, emptyDir for `/home/wiremock/__files`; ConfigMap `k6-scripts` from `k6.files`. App Deployment pods label `app.kubernetes.io/name=<service.name>` (Task 9 samples `kubectl top` with this selector).

- [ ] **Step 1: Failing tests** — render `helm template t engine/chart/loadtest-env -f <values from render_values(fixture plan)>` and parse docs:

```python
def test_chart_renders_expected_objects(): kinds/names include ResourceQuota/loadtest-quota, NetworkPolicy/default-deny-all, Deployment/postgres, Deployment/payments-api, Service/sample-api, ConfigMap/k6-scripts
def test_every_pod_is_restricted(): for every Deployment, each container has runAsNonRoot, drop ALL, seccomp RuntimeDefault, allowPrivilegeEscalation False, resources.limits
def test_default_deny_covers_ingress_and_egress(): policyTypes == ["Ingress","Egress"] and podSelector == {}
def test_no_postgres_when_database_disabled(): ...
def test_images_are_platform_pinned(): every non-service image ∈ PLATFORM_IMAGES.values()
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/cli/tests/test_chart.py -v` → FAIL. Implement → PASS; `helm lint engine/chart/loadtest-env -f <rendered values>` passes.
- [ ] **Step 3: Commit** `feat(chart): ephemeral test environment chart`.

### Task 8: Janitor (expired-namespace cleanup)

**Files:**
- Create: `engine/janitor/janitor.py` (stdlib only, runs in `python:3.12-alpine`)
- Test: `engine/janitor/test_janitor.py`

**Interfaces:**
- Produces:
  - `expired_namespaces(items: list[dict], now: int) -> list[str]` — items are Namespace objects from the API; returns names with label `loadtest.platform/managed=true`, `status.phase != "Terminating"`, and `expires-at` annotation `< now`. Missing/unparseable annotation counts as expired only if `metadata.creationTimestamp` is older than `MAX_DURATION_S + TTL_BUFFER_S` (1800 s) — constants duplicated here because the script ships standalone.
  - `main() -> int` — in-cluster: reads SA token/CA from `/var/run/secrets/kubernetes.io/serviceaccount/`, `GET /api/v1/namespaces?labelSelector=loadtest.platform/managed=true`, `DELETE` each expired one, logs one line per deletion, exits 0.

- [ ] **Step 1: Failing tests:**

```python
def test_expired_namespaces_selects_only_past_expiry_and_managed():
    items = [ns("old", expires=900), ns("fresh", expires=2000), ns("unmanaged", expires=1, managed=False), ns("dying", expires=1, phase="Terminating")]
    assert expired_namespaces(items, now=1000) == ["old"]
def test_missing_annotation_uses_creation_age(): ...
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/janitor -v` → FAIL. Implement → PASS.
- [ ] **Step 3: Commit** `feat(janitor): delete expired test namespaces`.

### Task 9: Orchestrator and CLI commands

**Files:**
- Create: `engine/cli/src/loadtest/kube.py`, `engine/cli/src/loadtest/orchestrator.py`, `engine/cli/src/loadtest/__main__.py`
- Test: `engine/cli/tests/test_orchestrator.py`, `engine/cli/tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `kube.py`: `Runner = Callable[[list[str], str | None], str]`; `def run(cmd: list[str], stdin: str | None = None) -> str` (subprocess, raises `CommandError(cmd, returncode, stderr)`); `parse_top(line: str) -> tuple[int, int]` ("sample-api-x 123m 45Mi" → (123, 45)).
  - `orchestrator.py`:
    - `class CapacityError(Exception)`, `class DeployError(Exception)`
    - `count_active_tests(runner: Runner) -> int` (managed namespaces not Terminating)
    - `wait_for_capacity(runner, max_tests: int, timeout_s: int = 600, poll_s: int = 15, sleep=time.sleep) -> None`
    - `provision(runner, plan: TestPlan, values: dict, chart_dir: Path) -> None` — `kubectl create namespace` via applied manifest with labels/annotations from Global Constraints, then `helm install loadtest <chart> -n <ns> -f - --wait --timeout 5m` (values YAML on stdin); wraps failures in `DeployError` including `kubectl get pods -n <ns>` output.
    - `run_job(runner, plan, phase, sample_peaks: bool, poll_s: int = 5, sleep=time.sleep) -> tuple[bool, str, int | None, int | None]` — applies `k6_job`, polls `.status.succeeded/failed` until done or deadline, samples `kubectl top pod -n <ns> -l app.kubernetes.io/name=<service> --no-headers` each poll when `sample_peaks` (ignore errors), returns (succeeded, logs, peak_cpu_m, peak_mem_mi).
    - `teardown(runner, namespace: str) -> None` — `kubectl delete namespace <ns> --ignore-not-found --wait=false`; never raises.
    - `@dataclass RunOutcome(exit_code: int, result: Result | None, markdown: str, html: str, summary: dict | None)`
    - `run_test(config_path: Path, image: str, run_id: str, out_dir: Path, runner: Runner = run, chart_dir: Path = …, k6_dir: Path = …, scheduling: dict | None = None, now: Callable[[], int] = …, sleep = time.sleep) -> RunOutcome` — sequence: load config (ConfigError → 2) → plan → `wait_for_capacity(MAX_CONCURRENT_TESTS)` → provision → smoke (failure → result with `smoke_passed=False`, exit 1, load skipped) → load with peak sampling → `extract_summary` (MissingResults → error report, exit 3) → `build_result` → write `out_dir/summary.md`, `report.html`, `k6-summary.json` → exit 0/1. **`teardown` runs in `finally` for every path after the namespace may have been created.** CapacityError/DeployError → error report, exit 3.
  - `__main__.py` (argparse): `loadtest validate CONFIG [--image]` (prints resolved plan as YAML), `loadtest estimate CONFIG [--image]` (prints markdown line), `loadtest run CONFIG --image IMG --run-id ID --out DIR [--scheduling FILE]`, `loadtest teardown --namespace NS`; `main() -> int`.

- [ ] **Step 1: Failing tests** with a `FakeRunner` (records commands; scripted responses keyed by command prefix; can be told to raise `CommandError` for a prefix):

```python
def test_run_happy_path_writes_reports_and_tears_down(tmp_path): outcome.exit_code == 0; files exist; last command is delete namespace
def test_namespace_manifest_has_labels_and_expiry(): ...  # PSS restricted, managed=true, team, expires-at
def test_run_smoke_failure_exits_1_skips_load_and_tears_down(): no "k6-load" apply; delete issued
def test_run_threshold_failure_exits_1(): ...
def test_run_missing_summary_exits_3_and_tears_down(): load logs lack markers → exit 3, markdown contains "no results collected"
def test_run_helm_failure_exits_3_and_tears_down(): helm install raises → exit 3, markdown names the deploy step
def test_capacity_wait_times_out(): count stays 3 → CapacityError after timeout (fake sleep advances clock)
def test_config_error_exits_2_without_touching_cluster(): no kubectl/helm commands recorded
def test_peak_sampling_keeps_maximum(): top outputs 100m/50Mi then 300m/40Mi → (300, 50)
def test_parse_top(): ...
```

`test_cli.py`: `main(["validate", fixture, "--image", "localhost:5001/x:1"]) == 0`; invalid config → 2 and errors printed to stderr.

- [ ] **Step 2: Run** `.venv/bin/pytest engine/cli/tests/test_orchestrator.py engine/cli/tests/test_cli.py -v` → FAIL. Implement → PASS. Full suite `.venv/bin/pytest engine -v` → PASS.
- [ ] **Step 3: Commit** `feat(cli): orchestrate end-to-end load test run`.

### Task 10: Local shared cluster — kind bootstrap, Terraform baseline module, Makefile, end-to-end run

**Files:**
- Create: `scripts/kind-up.sh`, `scripts/kind-down.sh`, `scripts/kind-config.yaml`, `infra/modules/k8s-platform-baseline/{main.tf,variables.tf,outputs.tf,versions.tf,README.md}`, `infra/envs/local-kind/{main.tf,versions.tf,variables.tf}`, `Makefile`

**Interfaces:**
- Consumes: `engine/janitor/janitor.py`, `PLATFORM_IMAGES`, `ALLOWED_REGISTRIES`.
- Produces:
  - `scripts/kind-up.sh` (idempotent): starts registry container `kind-registry` (`registry:2`) on `127.0.0.1:5001`; creates kind cluster `loadtest` with `disableDefaultCNI: true`, `podSubnet: 192.168.0.0/16`, containerd patch mapping `localhost:5001` → `http://kind-registry:5000`; connects registry to `kind` network; applies `local-registry-hosting` ConfigMap; installs Calico (pinned manifest URL, e.g. v3.28.2 `calico.yaml`) and waits for nodes Ready; installs metrics-server (pinned release, patched with `--kubelet-insecure-tls`); runs `terraform -chdir=infra/envs/local-kind init && apply -auto-approve`.
  - Module `k8s-platform-baseline` inputs: `namespace` (default `loadtest-system`), `janitor_schedule` (default `*/5 * * * *`), `janitor_script` (string), `allowed_image_prefixes` (list), `managed_label` (default `loadtest.platform/managed`). Creates: namespace; ServiceAccount + ClusterRole (`namespaces: list, delete`) + binding; ConfigMap with the script; CronJob running `python /janitor/janitor.py` in `python:3.12-alpine` with restricted securityContext; `ValidatingAdmissionPolicy` + binding (`admissionregistration.k8s.io/v1`, via `kubernetes_manifest`) rejecting Pod CREATE in namespaces labelled managed=true unless every container image starts with an allowed prefix. Outputs: `namespace`.
  - `infra/envs/local-kind`: kubernetes provider `config_path = "~/.kube/config"`, `config_context = "kind-loadtest"`; calls the module with `file("../../../engine/janitor/janitor.py")` and prefixes `["localhost:5001/", <three PLATFORM_IMAGES>]`. Local state (gitignored).
  - Makefile targets: `venv`, `test` (all pytest + ruff), `up`, `down`, `image` (build+push `localhost:5001/sample-api:$(TAG)`, `TAG` default `dev`), `loadtest` (`loadtest run services/sample-api/loadtest/loadtest.yaml --image localhost:5001/sample-api:$(TAG) --run-id $(RUN_ID) --out results/$(RUN_ID)`, `RUN_ID` default timestamp), `janitor-now` (`kubectl create job --from=cronjob/janitor`).

- [ ] **Step 1:** `terraform -chdir=infra/envs/local-kind fmt -check && terraform -chdir=infra/envs/local-kind init -backend=false && terraform -chdir=infra/envs/local-kind validate` → `Success!`.
- [ ] **Step 2: Bring up** `make up` → kind nodes Ready, `kubectl -n loadtest-system get cronjob janitor` exists, `kubectl top nodes` returns data (may take ~60 s).
- [ ] **Step 3: End-to-end** `make image loadtest` → exit 0, `results/<id>/summary.md` starts with `## ✅ PASS`, `kubectl get ns -l loadtest.platform/managed=true` empty afterwards (or Terminating).
- [ ] **Step 4: Guardrail checks (manual, record outputs for the README/demo):**
  - Admission policy: `kubectl create ns lt-probe --dry-run=client -o yaml | kubectl label --local -f - loadtest.platform/managed=true -o yaml | kubectl apply -f - && kubectl -n lt-probe run bad --image=docker.io/library/nginx` → denied by the policy; delete `lt-probe`.
  - Network isolation: during a run, `kubectl -n <ns> exec deploy/sample-api -- python -c "import urllib.request;urllib.request.urlopen('http://example.com',timeout=3)"` → times out.
  - Janitor: create a managed namespace with `expires-at` in the past, `make janitor-now`, namespace deleted.
  - Concurrency: start 4 `make loadtest` in parallel with `MAX_CONCURRENT_TESTS` 3 → 4th waits until one finishes.
  - Threshold failure: run with `p95_ms: 1` in a tmp copy of the config → exit 1, `## ❌ FAIL`.
- [ ] **Step 5:** Fix whatever the e2e reveals (expect WireMock/Postgres securityContext tweaks); keep Task 7 tests green.
- [ ] **Step 6: Commit** `feat(local): kind shared cluster, platform baseline module, Makefile`.

### Task 11: GCP Terraform modules (validated, not applied)

**Files:**
- Create: `infra/modules/gcp-network/`, `infra/modules/gcp-gke-cluster/`, `infra/modules/gcp-loadtest-nodepool/`, `infra/modules/gcp-budget/`, `infra/modules/gcp-github-wif/` (each `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, `README.md`), `infra/envs/gcp-loadtest/{main.tf,variables.tf,versions.tf,backend.tf,terraform.tfvars.example}`, `.tflint.hcl`, `engine/chart/loadtest-env/values-gke.example.yaml`

**Interfaces:**
- Modules (google provider `~> 6.0`, terraform `>= 1.9`):
  - `gcp-network(project_id, region, name)` → VPC, subnet with secondary ranges for pods/services, Cloud Router + NAT (egress for image pulls), **no peering**; outputs `network_id`, `subnet_id`, `pods_range_name`, `services_range_name`.
  - `gcp-gke-cluster(project_id, region, name, network_id, subnet_id, ranges…)` → private regional cluster, Dataplane V2 (network policy enforcement), Workload Identity, release channel REGULAR, default pool removed; outputs `cluster_name`, `endpoint`.
  - `gcp-loadtest-nodepool(cluster, machine_type = "e2-standard-4", max_nodes = 6)` → spot VMs, autoscaling min 0, taint `dedicated=loadtest:NoSchedule`, label `workload=loadtest`.
  - `gcp-budget(billing_account, project_id, amount_usd = 300)` → alerts at 50/90/100 %.
  - `gcp-github-wif(project_id, github_repository_owner, cluster_name)` → pool+provider with `attribute_condition` pinned to the org, service account with `roles/container.developer` only in the load-test project, binding for `principalSet` of allowed repos.
  - `envs/gcp-loadtest` composes all five in a **dedicated project** (`var.project_id`, documented as separate from prod); `backend.tf` is a commented GCS backend.
  - `values-gke.example.yaml`: `scheduling` with nodeSelector `workload: loadtest` and toleration for the taint (passed via `loadtest run --scheduling`).

- [ ] **Step 1:** Write modules. **Step 2: Verify** for each module and the env: `terraform fmt -check -recursive infra`, `terraform -chdir=<dir> init -backend=false && terraform -chdir=<dir> validate` → `Success!`; `tflint --recursive` → no errors; `trivy config infra` (or `checkov -d infra`) → no HIGH/CRITICAL, or each remaining one suppressed inline with a one-line justification.
- [ ] **Step 3: Commit** `feat(infra): GCP modules for the load-test platform (not applied)`.

### Task 12: GitHub Actions — CI, reusable load-test workflow, sample caller

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/loadtest-reusable.yml`, `.github/workflows/sample-api-loadtest.yml`

**Interfaces:**
- `loadtest-reusable.yml` (`on: workflow_call`), inputs: `config-path` (string, required), `image` (string, required, e.g. `localhost:5001/sample-api:<sha>`), `image-artifact` (string, optional — name of an uploaded `docker save` tar; prototype stand-in for "image already in registry"), `service` (string, required; used for the concurrency group). Permissions: `contents: read`, `pull-requests: write`, `id-token: write` (commented: for WIF on GKE). Concurrency: `group: loadtest-${{ inputs.service }}`, `cancel-in-progress: false`. Steps: checkout → setup Python 3.12 → `pip install -e engine/cli` → `loadtest validate` (fails fast) → `loadtest estimate >> $GITHUB_STEP_SUMMARY` → install pinned kind/helm/terraform → `scripts/kind-up.sh` → download + `docker load` + `docker push` image if `image-artifact` → `loadtest run ... --run-id ${{ github.run_id }}-${{ github.run_attempt }} --out results` (`id: run`, `continue-on-error: true`) → always: `cat results/summary.md >> $GITHUB_STEP_SUMMARY` → always: upload `results/` artifact `loadtest-results` → if PR event: comment `results/summary.md` via `gh pr comment --edit-last || gh pr comment` → always: `loadtest teardown` (belt-and-braces) → final step fails the job if `steps.run.outcome != 'success'`. Comment in file: on GKE, replace kind-up with `google-github-actions/auth` (WIF) + `get-gke-credentials` and pass `--scheduling values-gke.yaml`.
- `sample-api-loadtest.yml`: `on: workflow_dispatch` and `pull_request: types [labeled, synchronize]` with job-level `if` requiring label `loadtest` **and** `github.event.pull_request.head.repo.full_name == github.repository` (forks excluded). Job `build`: build image, `docker save`, upload artifact. Job `loadtest`: `uses: ./.github/workflows/loadtest-reusable.yml` with `config-path: services/sample-api/loadtest/loadtest.yaml`, `service: sample-api`.
- `ci.yml` (push + pull_request): jobs `python` (ruff + pytest for `engine/` and `services/sample-api`), `helm` (install helm, `helm lint` with values rendered by `loadtest` from the fixture, run `test_chart.py`), `terraform` (fmt check, validate every env and module with `-backend=false`, tflint, trivy config).

- [ ] **Step 1:** Write workflows. **Step 2: Verify** `actionlint` (install via `brew install actionlint` or the release binary) → no errors.
- [ ] **Step 3: Commit** `ci: CI, reusable load-test workflow, sample caller`.
- [ ] **Step 4: Remote run (needs the candidate).** Ask the candidate to create a GitHub repo and push (`gh repo create <name> --private --source . --push`), then trigger `sample-api-loadtest` via `gh workflow run sample-api-loadtest.yml` and watch with `gh run watch`. Expected: CI green; load-test run green with the PASS table in the job summary and a `loadtest-results` artifact. Fix and re-push until green.

### Task 13: Documentation — README, WRITEUP, AI-USAGE

**Files:**
- Create: `README.md`, `WRITEUP.md`, `AI-USAGE.md`, `engine/README.md` (developer guide: `loadtest.yaml` reference, sizes, caps, results)

- [ ] **Step 1: README** — what it is (3 lines); prerequisites (Docker, kind, kubectl, helm, terraform, Python 3.12); quickstart `make venv test up image loadtest`; running in GitHub Actions; repo map; guardrails table (real on kind vs Terraform only, copied from spec §7); demo script for the video (the Task 10 Step 4 checks in order); teardown `make down`.
- [ ] **Step 2: WRITEUP.md** (≤ 3 pages, ~1,200 words) using spec §14 themes: assumed context (incl. no cloud account, kind as GKE stand-in, internal repos without forks); alternatives with pick + why for env provisioning, load generator, IaC layout, developer interface; trade-offs (shared cluster blast radius, stubs vs realism, kind fidelity, results-at-end vs live metrics); next week list. Verify page budget: `wc -w WRITEUP.md` ≤ 1,400.
- [ ] **Step 3: AI-USAGE.md** — tools (Claude Code with superpowers skills: brainstorming → design spec → this plan → execution with per-task review), representative prompts (quote from the session), agentic workflow (spec/plan files under `docs/superpowers/`), validation (TDD unit tests, helm/terraform validate, tflint/trivy, actionlint, kind e2e, manual guardrail probes). Leave clearly marked `TODO(candidate)` spots for anything only the candidate can state (other tools they used, personal review notes).
- [ ] **Step 4: Verify** every command in README runs as written on a fresh `make down && make up`.
- [ ] **Step 5: Commit** `docs: README, WRITEUP, AI-USAGE`.
