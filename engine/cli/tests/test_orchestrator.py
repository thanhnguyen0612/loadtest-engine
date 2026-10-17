import json
import shutil
from pathlib import Path

import pytest

from loadtest import limits, orchestrator
from loadtest.config import load_config
from loadtest.kube import CommandError, parse_top
from loadtest.orchestrator import (
    CapacityError,
    count_active_tests,
    namespace_manifest,
    run_job,
    run_test,
    teardown,
    wait_for_capacity,
)
from loadtest.plan import build_plan
from loadtest.report import SUMMARY_BEGIN, SUMMARY_END

FIX = Path(__file__).parent / "fixtures"
CONFIG = FIX / "valid" / "loadtest.yaml"
PASS = json.loads((FIX / "k6_summary_pass.json").read_text())
FAIL = json.loads((FIX / "k6_summary_fail.json").read_text())
IMAGE = "localhost:5001/sample-api:test"
NOW = 1_000_000
NS = "lt-sample-api-42"


@pytest.fixture(autouse=True)
def _default_concurrency(monkeypatch):
    monkeypatch.delenv("LOADTEST_MAX_CONCURRENT_TESTS", raising=False)


def logs(summary: dict) -> str:
    return f"running...\n{SUMMARY_BEGIN}\n{json.dumps(summary)}\n{SUMMARY_END}\n"


def job_status(succeeded=None, failed=None) -> str:
    status = {}
    if succeeded:
        status["succeeded"] = succeeded
    if failed:
        status["failed"] = failed
    return json.dumps({"status": status})


def namespaces(*phases: str) -> str:
    return json.dumps({"items": [{"metadata": {"name": f"lt-x-{i}"}, "status": {"phase": p}}
                                 for i, p in enumerate(phases)]})


class FakeRunner:
    """Records commands. Responses are keyed by command prefix (longest match wins);
    a list is consumed in order (the last entry repeats); an Exception is raised."""

    def __init__(self, responses: dict | None = None):
        self.responses = {
            "kubectl get namespaces": namespaces(),
            "kubectl get job k6-smoke": job_status(succeeded=1),
            "kubectl get job k6-load": job_status(succeeded=1),
            "kubectl logs job/k6-smoke": logs(PASS),
            "kubectl logs job/k6-load": logs(PASS),
            "kubectl top pod": f"{NS}-app 120m 80Mi",
        }
        self.responses.update(responses or {})
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, cmd: list[str], stdin: str | None = None) -> str:
        self.calls.append((cmd, stdin))
        line = " ".join(cmd)
        matches = [p for p in self.responses if line.startswith(p)]
        if not matches:
            return ""
        resp = self.responses[max(matches, key=len)]
        if isinstance(resp, list):
            resp = resp.pop(0) if len(resp) > 1 else resp[0]
        if isinstance(resp, Exception):
            raise resp
        return resp

    def lines(self) -> list[str]:
        return [" ".join(c) for c, _ in self.calls]

    def stdin_for(self, prefix: str) -> list[str]:
        return [s for c, s in self.calls if " ".join(c).startswith(prefix)]


def fail(cmd: str, stderr: str = "boom") -> CommandError:
    return CommandError(cmd.split(), 1, stderr)


def no_sleep(_s):
    raise AssertionError("unexpected sleep")


def k6_dir(tmp_path: Path) -> Path:
    d = tmp_path / "k6"
    d.mkdir()
    (d / "runner.js").write_text("// runner")
    (d / "endpoints.js").write_text("// endpoints")
    return d


def go(tmp_path, runner, config=CONFIG, sleep=no_sleep):
    return run_test(config, IMAGE, "42", tmp_path / "out", runner=runner,
                    chart_dir=tmp_path / "chart", k6_dir=k6_dir(tmp_path),
                    now=lambda: NOW, sleep=sleep)


def applied_kinds(runner: FakeRunner) -> list[tuple[str, str]]:
    out = []
    for stdin in runner.stdin_for("kubectl apply"):
        m = json.loads(stdin)
        out.append((m["kind"], m["metadata"]["name"]))
    return out


def test_run_happy_path_writes_reports_and_tears_down(tmp_path):
    runner = FakeRunner()
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 0
    assert outcome.result.passed
    assert (outcome.result.peak_cpu_m, outcome.result.peak_mem_mi) == (120, 80)
    out = tmp_path / "out"
    assert (out / "summary.md").read_text() == outcome.markdown
    assert (out / "report.html").read_text() == outcome.html
    assert json.loads((out / "k6-summary.json").read_text()) == PASS
    assert "PASS" in outcome.markdown and "est. cost" in outcome.markdown
    assert applied_kinds(runner) == [
        ("Namespace", NS), ("Job", "k6-smoke"), ("Job", "k6-load")]
    helm = [c for c, _ in runner.calls if c[0] == "helm"]
    assert helm == [["helm", "install", "loadtest", str(tmp_path / "chart"), "-n", NS,
                     "-f", "-", "--wait", "--timeout", "5m"]]
    (values_yaml,) = runner.stdin_for("helm install")
    assert f"image: {IMAGE}" in values_yaml
    assert runner.lines()[-1] == f"kubectl delete namespace {NS} --ignore-not-found --wait=false"
    top = [line for line in runner.lines() if line.startswith("kubectl top")]
    assert top and all("-l app.kubernetes.io/name=sample-api" in t for t in top)
    assert all(f"-n {NS}" in t for t in top)


def test_namespace_manifest_has_labels_and_expiry():
    plan = build_plan(load_config(CONFIG), "42", now=NOW)
    m = namespace_manifest(plan)
    assert (m["apiVersion"], m["kind"], m["metadata"]["name"]) == ("v1", "Namespace", NS)
    labels = m["metadata"]["labels"]
    assert labels[limits.MANAGED_LABEL] == "true"
    assert labels[limits.TEAM_LABEL] == "orders"
    assert labels[limits.SERVICE_LABEL] == "sample-api"
    assert labels[limits.RUN_ID_LABEL] == "42"
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"
    assert labels["pod-security.kubernetes.io/enforce-version"] == "latest"
    expires = m["metadata"]["annotations"][limits.EXPIRES_ANNOTATION]
    assert expires == str(NOW + 60 + 15 + 300 + limits.TTL_BUFFER_S)


def test_capacity_wait_does_not_shorten_namespace_lifetime(tmp_path):
    busy = namespaces("Active", "Active", "Active")
    runner = FakeRunner({"kubectl get namespaces": [busy, busy, busy, namespaces()]})
    clock = [NOW]

    def sleep(s):
        clock[0] += s

    outcome = run_test(CONFIG, IMAGE, "42", tmp_path / "out", runner=runner,
                       chart_dir=tmp_path / "chart", k6_dir=k6_dir(tmp_path),
                       now=lambda: clock[0], sleep=sleep)
    assert outcome.exit_code == 0
    waited = 45
    (ns_manifest,) = [json.loads(s) for s in runner.stdin_for("kubectl apply")
                      if json.loads(s)["kind"] == "Namespace"]
    expires = int(ns_manifest["metadata"]["annotations"][limits.EXPIRES_ANNOTATION])
    # The full lifetime starts when the namespace is created, after the queue.
    assert expires == NOW + waited + 60 + 15 + 300 + limits.TTL_BUFFER_S


def test_label_keys_match_global_constraints():
    assert limits.MANAGED_LABEL == "loadtest.platform/managed"
    assert limits.TEAM_LABEL == "loadtest.platform/team"
    assert limits.SERVICE_LABEL == "loadtest.platform/service"
    assert limits.RUN_ID_LABEL == "loadtest.platform/run-id"
    assert limits.EXPIRES_ANNOTATION == "loadtest.platform/expires-at"


def test_run_smoke_failure_exits_1_skips_load_and_tears_down(tmp_path):
    runner = FakeRunner({"kubectl get job k6-smoke": job_status(failed=1),
                         "kubectl logs job/k6-smoke": logs(FAIL)})
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 1
    assert outcome.result.smoke_passed is False and not outcome.result.passed
    assert ("Job", "k6-load") not in applied_kinds(runner)
    assert not any("k6-load" in line for line in runner.lines())
    assert "smoke test failed" in outcome.markdown
    assert runner.lines()[-1].startswith(f"kubectl delete namespace {NS}")


def test_run_smoke_failure_without_summary_exits_3(tmp_path):
    runner = FakeRunner({"kubectl get job k6-smoke": job_status(failed=1),
                         "kubectl logs job/k6-smoke": "OOMKilled"})
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 3
    assert "no results collected" in outcome.markdown
    assert runner.lines()[-1].startswith(f"kubectl delete namespace {NS}")


def test_run_threshold_failure_exits_1(tmp_path):
    runner = FakeRunner({"kubectl get job k6-load": job_status(failed=1),
                         "kubectl logs job/k6-load": logs(FAIL)})
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 1
    assert outcome.result.smoke_passed and not outcome.result.passed
    assert "FAIL" in outcome.markdown
    assert (tmp_path / "out" / "k6-summary.json").exists()
    assert runner.lines()[-1].startswith(f"kubectl delete namespace {NS}")


def test_run_missing_summary_exits_3_and_tears_down(tmp_path):
    runner = FakeRunner({"kubectl get job k6-load": job_status(failed=1),
                         "kubectl logs job/k6-load": "k6 OOMKilled"})
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 3
    assert outcome.result is None and outcome.summary is None
    assert "no results collected" in outcome.markdown
    assert "markers not found" in outcome.markdown
    assert (tmp_path / "out" / "summary.md").exists()
    assert not (tmp_path / "out" / "k6-summary.json").exists()
    assert runner.lines()[-1].startswith(f"kubectl delete namespace {NS}")


def test_run_incomplete_summary_exits_3(tmp_path):
    runner = FakeRunner({"kubectl logs job/k6-load": logs({"metrics": {}})})
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 3
    assert "no results collected" in outcome.markdown
    assert "missing metric" in outcome.markdown
    assert runner.lines()[-1].startswith(f"kubectl delete namespace {NS}")


def test_run_logs_unavailable_exits_3(tmp_path):
    runner = FakeRunner({"kubectl logs job/k6-load": fail("kubectl logs", "pod gone")})
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 3
    assert "no results collected" in outcome.markdown
    assert runner.lines()[-1].startswith(f"kubectl delete namespace {NS}")


def test_run_helm_failure_exits_3_and_tears_down(tmp_path):
    runner = FakeRunner({
        "helm install": fail("helm install", "timed out waiting for the condition"),
        "kubectl get pods": "sample-api-abc 0/1 CrashLoopBackOff",
    })
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 3
    assert outcome.result is None
    assert "helm install" in outcome.markdown
    assert "timed out waiting for the condition" in outcome.markdown
    assert "CrashLoopBackOff" in outcome.markdown
    assert not any("k6-smoke" in line for line in runner.lines())
    assert runner.lines()[-1].startswith(f"kubectl delete namespace {NS}")


def test_run_namespace_apply_failure_exits_3_and_tears_down(tmp_path):
    runner = FakeRunner({"kubectl apply": fail("kubectl apply", "forbidden")})
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 3
    assert "namespace" in outcome.markdown and "forbidden" in outcome.markdown
    assert runner.lines()[-1].startswith(f"kubectl delete namespace {NS}")


def test_run_teardown_failure_does_not_change_outcome(tmp_path):
    runner = FakeRunner({"kubectl delete": fail("kubectl delete", "unreachable")})
    assert go(tmp_path, runner).exit_code == 0


def test_run_capacity_timeout_exits_3_without_creating_namespace(tmp_path):
    runner = FakeRunner({"kubectl get namespaces": namespaces("Active", "Active", "Active")})
    slept = []
    outcome = go(tmp_path, runner, sleep=slept.append)
    assert outcome.exit_code == 3
    assert "capacity" in outcome.markdown.lower()
    assert sum(slept) >= 600
    assert all(line.startswith("kubectl get namespaces") for line in runner.lines())


def test_capacity_wait_times_out():
    runner = FakeRunner({"kubectl get namespaces": namespaces("Active", "Active", "Active")})
    clock = [0]

    def sleep(s):
        clock[0] += s

    with pytest.raises(CapacityError):
        wait_for_capacity(runner, max_tests=3, timeout_s=60, poll_s=15, sleep=sleep)
    assert clock[0] == 60


def test_capacity_wait_returns_when_slot_frees():
    runner = FakeRunner({"kubectl get namespaces": [
        namespaces("Active", "Active", "Active"), namespaces("Active", "Active")]})
    slept = []
    wait_for_capacity(runner, max_tests=3, timeout_s=60, poll_s=15, sleep=slept.append)
    assert slept == [15]


def test_count_active_tests_ignores_terminating():
    runner = FakeRunner({"kubectl get namespaces": namespaces("Active", "Terminating")})
    assert count_active_tests(runner) == 1
    (cmd, _), = runner.calls
    assert "-l" in cmd and f"{limits.MANAGED_LABEL}=true" in cmd


def test_config_error_exits_2_without_touching_cluster(tmp_path):
    bad = tmp_path / "loadtest.yaml"
    bad.write_text("service: {name: Bad_Name, team: t}\nload: {endpoints: ['GET http://x']}\n")
    runner = FakeRunner()
    outcome = run_test(bad, "docker.io/evil:1", "42", tmp_path / "out", runner=runner,
                       k6_dir=k6_dir(tmp_path), now=lambda: NOW, sleep=no_sleep)
    assert outcome.exit_code == 2
    assert runner.calls == []
    assert "service.name" in outcome.markdown and "registry not allowed" in outcome.markdown
    assert (tmp_path / "out" / "summary.md").exists()


def test_malformed_stub_json_exits_2_without_touching_cluster(tmp_path):
    cfg_dir = tmp_path / "cfg"
    shutil.copytree(FIX / "valid", cfg_dir)
    (cfg_dir / "stubs" / "payments-api" / "charge.json").write_text("{not json")
    runner = FakeRunner()
    outcome = go(tmp_path, runner, config=cfg_dir / "loadtest.yaml")
    assert outcome.exit_code == 2
    assert runner.calls == []
    assert "charge.json" in outcome.markdown or "Expecting" in outcome.markdown


def test_invalid_run_id_exits_2(tmp_path):
    runner = FakeRunner()
    outcome = run_test(CONFIG, IMAGE, "bad id!", tmp_path / "out", runner=runner,
                       k6_dir=k6_dir(tmp_path), now=lambda: NOW, sleep=no_sleep)
    assert outcome.exit_code == 2
    assert runner.calls == []


def test_peak_sampling_keeps_maximum():
    plan = build_plan(load_config(CONFIG), "42", now=NOW)
    runner = FakeRunner({
        "kubectl get job k6-load": [job_status(), job_status(succeeded=1)],
        "kubectl top pod": [f"{NS}-app 100m 50Mi", f"{NS}-app 300m 40Mi"],
        "kubectl logs job/k6-load": "LOGS",
    })
    slept = []
    ok, out, cpu, mem = run_job(runner, plan, "load", sample_peaks=True, poll_s=5,
                                sleep=slept.append)
    assert (ok, out, cpu, mem) == (True, "LOGS", 300, 50)
    assert slept == [5]


def test_peak_sampling_ignores_errors():
    plan = build_plan(load_config(CONFIG), "42", now=NOW)
    runner = FakeRunner({
        "kubectl get job k6-load": [job_status(), job_status(succeeded=1)],
        "kubectl top pod": [fail("kubectl top", "metrics not available"), "garbage"],
    })
    ok, _, cpu, mem = run_job(runner, plan, "load", sample_peaks=True, sleep=lambda s: None)
    assert ok and (cpu, mem) == (None, None)


def test_run_job_without_sampling_never_calls_top():
    plan = build_plan(load_config(CONFIG), "42", now=NOW)
    runner = FakeRunner()
    ok, _, cpu, mem = run_job(runner, plan, "smoke", sample_peaks=False, sleep=no_sleep)
    assert ok and (cpu, mem) == (None, None)
    assert not any(line.startswith("kubectl top") for line in runner.lines())


def test_run_job_gives_up_at_deadline():
    plan = build_plan(load_config(CONFIG), "42", now=NOW)
    runner = FakeRunner({"kubectl get job k6-smoke": job_status()})
    clock = [0]

    def sleep(s):
        clock[0] += s

    ok, _, _, _ = run_job(runner, plan, "smoke", sample_peaks=False, poll_s=5, sleep=sleep)
    assert ok is False
    deadline = 15 + 60  # smoke activeDeadlineSeconds
    assert deadline < clock[0] <= deadline + orchestrator.JOB_GRACE_S + 5


def test_run_job_passes_scheduling_to_job():
    plan = build_plan(load_config(CONFIG), "42", now=NOW)
    runner = FakeRunner()
    sched = {"nodeSelector": {"pool": "loadtest"}, "tolerations": []}
    run_job(runner, plan, "smoke", sample_peaks=False, sleep=no_sleep, scheduling=sched)
    (stdin,) = runner.stdin_for("kubectl apply")
    assert json.loads(stdin)["spec"]["template"]["spec"]["nodeSelector"] == {"pool": "loadtest"}


def test_teardown_never_raises():
    runner = FakeRunner({"kubectl delete": RuntimeError("kaboom")})
    teardown(runner, NS)
    assert runner.lines() == [f"kubectl delete namespace {NS} --ignore-not-found --wait=false"]


def test_parse_top():
    assert parse_top("sample-api-x 123m 45Mi") == (123, 45)
    assert parse_top("sample-api-x   2   1Gi  ") == (2000, 1024)
    with pytest.raises(ValueError):
        parse_top("garbage")


def test_default_dirs_resolve_in_checkout(monkeypatch):
    monkeypatch.delenv("LOADTEST_CHART_DIR", raising=False)
    monkeypatch.delenv("LOADTEST_K6_DIR", raising=False)
    assert (orchestrator.default_chart_dir() / "Chart.yaml").is_file()
    assert (orchestrator.default_k6_dir() / "runner.js").is_file()
    monkeypatch.setenv("LOADTEST_CHART_DIR", "/x/chart")
    monkeypatch.setenv("LOADTEST_K6_DIR", "/x/k6")
    assert orchestrator.default_chart_dir() == Path("/x/chart")
    assert orchestrator.default_k6_dir() == Path("/x/k6")


def test_run_missing_summary_reports_pods(tmp_path):
    # Seen on a full node: the k6 pod stays Pending until activeDeadlineSeconds, so there
    # are no logs; the report must show why (pod states), not only "markers not found".
    runner = FakeRunner({
        "kubectl get job k6-smoke": job_status(failed=1),
        "kubectl logs job/k6-smoke": "",
        "kubectl get pods": "k6-smoke-dmf4c 0/1 Pending",
    })
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 3
    assert "no results collected (smoke phase)" in outcome.markdown
    assert "k6-smoke-dmf4c 0/1 Pending" in outcome.markdown
    assert runner.lines()[-1].startswith(f"kubectl delete namespace {NS}")


def test_concurrency_cap_defaults_to_limit(tmp_path):
    # 2 active < default 3: no waiting (no_sleep would raise).
    runner = FakeRunner({"kubectl get namespaces": namespaces("Active", "Active")})
    assert go(tmp_path, runner).exit_code == 0


def test_concurrency_cap_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADTEST_MAX_CONCURRENT_TESTS", "1")
    runner = FakeRunner({"kubectl get namespaces": [namespaces("Active"), namespaces()]})
    slept = []
    outcome = go(tmp_path, runner, sleep=slept.append)
    assert outcome.exit_code == 0
    assert slept == [15]


@pytest.mark.parametrize("value", ["0", "-1", "two", ""])
def test_concurrency_cap_env_invalid_exits_2_without_touching_cluster(tmp_path, monkeypatch,
                                                                      value):
    monkeypatch.setenv("LOADTEST_MAX_CONCURRENT_TESTS", value)
    runner = FakeRunner()
    outcome = go(tmp_path, runner)
    assert outcome.exit_code == 2
    assert "LOADTEST_MAX_CONCURRENT_TESTS" in outcome.markdown
    assert runner.calls == []
