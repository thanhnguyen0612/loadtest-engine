"""End-to-end test run: capacity gate, provision, smoke, load, report, teardown.

All cluster interaction goes through an injectable `Runner` (default: `kube.run`, which
shells out to kubectl/helm), so the sequence is testable without a cluster.

The Helm chart and k6 scripts are located relative to this package, which works in a repo
checkout (`engine/chart/loadtest-env` and `engine/k6`). Override them with the environment
variables `LOADTEST_CHART_DIR` and `LOADTEST_K6_DIR` (e.g. for a non-editable install).
"""

import html as _html
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from loadtest import limits
from loadtest.config import ConfigError, load_config
from loadtest.estimate import estimate_cost
from loadtest.k6job import k6_job
from loadtest.kube import CommandError, Runner, parse_top, run
from loadtest.plan import TestPlan, build_plan
from loadtest.report import (
    Result,
    build_result,
    extract_summary,
    render_html,
    render_markdown,
)
from loadtest.values import render_values

ENGINE_DIR = Path(__file__).resolve().parents[3]
HELM_RELEASE = "loadtest"
HELM_TIMEOUT = "5m"
JOB_GRACE_S = 60  # client-side wait beyond the Job's activeDeadlineSeconds
# Kubernetes label value: run-id is stamped on namespaces and pods.
RUN_ID = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$")


class CapacityError(Exception):
    """Too many tests already running; gave up waiting for a slot."""


class DeployError(Exception):
    """Creating the namespace or installing the chart failed."""


def default_chart_dir() -> Path:
    return Path(os.environ.get("LOADTEST_CHART_DIR") or ENGINE_DIR / "chart" / "loadtest-env")


def default_k6_dir() -> Path:
    return Path(os.environ.get("LOADTEST_K6_DIR") or ENGINE_DIR / "k6")


def count_active_tests(runner: Runner) -> int:
    out = runner(["kubectl", "get", "namespaces", "-l", f"{limits.MANAGED_LABEL}=true",
                  "-o", "json"], None)
    items = json.loads(out or "{}").get("items", [])
    return sum(1 for i in items if (i.get("status") or {}).get("phase") != "Terminating")


def wait_for_capacity(runner: Runner, max_tests: int, timeout_s: int = 600, poll_s: int = 15,
                      sleep=time.sleep) -> None:
    waited = 0
    while (active := count_active_tests(runner)) >= max_tests:
        if waited >= timeout_s:
            raise CapacityError(
                f"platform at capacity: {active} tests running (max {max_tests}); "
                f"gave up after waiting {waited}s")
        print(f"{active} tests running (max {max_tests}); waiting for a slot...",
              file=sys.stderr, flush=True)
        sleep(poll_s)
        waited += poll_s


def namespace_manifest(plan: TestPlan) -> dict:
    svc = plan.config.service
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": plan.namespace,
            "labels": {
                limits.MANAGED_LABEL: "true",
                limits.TEAM_LABEL: svc.team,
                limits.SERVICE_LABEL: svc.name,
                limits.RUN_ID_LABEL: plan.run_id,
                "pod-security.kubernetes.io/enforce": "restricted",
                "pod-security.kubernetes.io/enforce-version": "latest",
            },
            "annotations": {limits.EXPIRES_ANNOTATION: str(plan.expires_at)},
        },
    }


def _pods(runner: Runner, namespace: str) -> str:
    try:
        return runner(["kubectl", "get", "pods", "-n", namespace, "-o", "wide"], None).strip()
    except CommandError as exc:  # diagnostics only
        return f"(could not list pods: {exc})"


def provision(runner: Runner, plan: TestPlan, values: dict, chart_dir: Path) -> None:
    ns = plan.namespace
    try:
        runner(["kubectl", "apply", "-f", "-"], json.dumps(namespace_manifest(plan)))
    except CommandError as exc:
        raise DeployError(f"deploy step 'create namespace {ns}' failed: {exc}") from exc
    try:
        runner(["helm", "install", HELM_RELEASE, str(chart_dir), "-n", ns, "-f", "-",
                "--wait", "--timeout", HELM_TIMEOUT], yaml.safe_dump(values, sort_keys=False))
    except CommandError as exc:
        raise DeployError(
            f"deploy step 'helm install' failed (service or dependencies never became "
            f"ready within {HELM_TIMEOUT}?): {exc}\n\nPods in {ns}:\n{_pods(runner, ns)}"
        ) from exc


def _job_state(runner: Runner, name: str, ns: str) -> bool | None:
    """True succeeded, False failed, None still running (or status unreadable)."""
    try:
        status = json.loads(runner(["kubectl", "get", "job", name, "-n", ns, "-o", "json"],
                                   None)).get("status") or {}
    except (CommandError, ValueError):
        return None
    if status.get("succeeded"):
        return True
    if status.get("failed"):
        return False
    return None


def _sample(runner: Runner, plan: TestPlan) -> list[tuple[int, int]]:
    try:
        out = runner(["kubectl", "top", "pod", "-n", plan.namespace, "-l",
                      f"app.kubernetes.io/name={plan.config.service.name}", "--no-headers"],
                     None)
        return [parse_top(line) for line in out.splitlines() if line.strip()]
    except (CommandError, ValueError):  # metrics-server may lag; peaks are best effort
        return []


def run_job(runner: Runner, plan: TestPlan, phase: Literal["smoke", "load"],
            sample_peaks: bool, poll_s: int = 5, sleep=time.sleep,
            scheduling: dict | None = None) -> tuple[bool, str, int | None, int | None]:
    job = k6_job(plan, phase, scheduling)
    name, ns = job["metadata"]["name"], plan.namespace
    runner(["kubectl", "apply", "-f", "-"], json.dumps(job))
    deadline = job["spec"]["activeDeadlineSeconds"] + JOB_GRACE_S
    peak_cpu = peak_mem = None
    waited = 0
    while True:
        if sample_peaks:
            for cpu, mem in _sample(runner, plan):
                peak_cpu = cpu if peak_cpu is None else max(peak_cpu, cpu)
                peak_mem = mem if peak_mem is None else max(peak_mem, mem)
        state = _job_state(runner, name, ns)
        if state is not None:
            succeeded = state
            break
        if waited > deadline:
            succeeded = False
            break
        sleep(poll_s)
        waited += poll_s
    try:
        logs = runner(["kubectl", "logs", f"job/{name}", "-n", ns], None)
    except CommandError as exc:
        logs = f"(could not read logs: {exc})"
    return succeeded, logs, peak_cpu, peak_mem


def teardown(runner: Runner, namespace: str) -> None:
    try:
        runner(["kubectl", "delete", "namespace", namespace, "--ignore-not-found",
                "--wait=false"], None)
    except Exception as exc:  # noqa: BLE001 -- never raise; the janitor deletes it after expires-at
        print(f"warning: teardown of {namespace} failed: {exc}", file=sys.stderr, flush=True)


@dataclass
class RunOutcome:
    exit_code: int
    result: Result | None
    markdown: str
    html: str
    summary: dict | None


def _config_error_outcome(errors: list[str]) -> RunOutcome:
    md = "\n".join(["## ⚠️ CONFIG INVALID", "", *[f"- {e}" for e in errors], ""])
    items = "".join(f"<li>{_html.escape(e)}</li>" for e in errors)
    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>CONFIG INVALID</title></head><body><h1>⚠️ CONFIG INVALID</h1>"
            f"<ul>{items}</ul></body></html>")
    return RunOutcome(2, None, md, page, None)


def _write(out_dir: Path, outcome: RunOutcome) -> RunOutcome:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.md").write_text(outcome.markdown)
    (out_dir / "report.html").write_text(outcome.html)
    if outcome.summary is not None:
        (out_dir / "k6-summary.json").write_text(json.dumps(outcome.summary, indent=2))
    return outcome


def _now() -> int:
    return int(time.time())


def run_test(config_path: Path, image: str, run_id: str, out_dir: Path, runner: Runner = run,
             chart_dir: Path | None = None, k6_dir: Path | None = None,
             scheduling: dict | None = None, now: Callable[[], int] = _now,
             sleep=time.sleep) -> RunOutcome:
    out_dir = Path(out_dir)
    chart_dir = Path(chart_dir) if chart_dir else default_chart_dir()
    k6_dir = Path(k6_dir) if k6_dir else default_k6_dir()

    if not RUN_ID.match(run_id):
        return _write(out_dir, _config_error_outcome(
            [f"run id {run_id!r} must be a valid Kubernetes label value"]))
    try:
        cfg = load_config(Path(config_path), image)
    except ConfigError as exc:
        return _write(out_dir, _config_error_outcome(exc.errors))
    plan = build_plan(cfg, run_id, now())
    est = estimate_cost(plan)
    try:
        values = render_values(plan, k6_dir, scheduling)
    except (ValueError, OSError) as exc:
        return _write(out_dir, _config_error_outcome([f"cannot render test environment: {exc}"]))

    def outcome(code: int, result: Result | None, summary: dict | None = None,
                error: str | None = None) -> RunOutcome:
        return _write(out_dir, RunOutcome(code, result, render_markdown(result, plan, est, error),
                                          render_html(result, plan, est, error), summary))

    try:
        wait_for_capacity(runner, limits.MAX_CONCURRENT_TESTS, sleep=sleep)
    except (CapacityError, CommandError) as exc:
        return outcome(3, None, error=str(exc))

    try:
        provision(runner, plan, values, chart_dir)
        smoke_ok, smoke_logs, _, _ = run_job(runner, plan, "smoke", sample_peaks=False,
                                             sleep=sleep, scheduling=scheduling)
        if smoke_ok:
            phase = "load"
            # The k6 summary, not the Job status, decides pass/fail (k6 exits non-zero on
            # threshold failure); a missing summary is handled below.
            _, logs, peak_cpu, peak_mem = run_job(runner, plan, "load", sample_peaks=True,
                                                  sleep=sleep, scheduling=scheduling)
        else:
            phase, logs, peak_cpu, peak_mem = "smoke", smoke_logs, None, None
        try:
            summary = extract_summary(logs)
            result = build_result(summary, plan, peak_cpu, peak_mem, smoke_passed=smoke_ok)
        except Exception as exc:  # noqa: BLE001 -- any bad/partial summary means no results
            return outcome(3, None, error=f"no results collected ({phase} phase): {exc}"
                                          f"\n\nPods in {plan.namespace}:\n"
                                          f"{_pods(runner, plan.namespace)}")
        return outcome(0 if result.passed else 1, result, summary)
    except (DeployError, CommandError) as exc:
        return outcome(3, None, error=str(exc))
    finally:
        teardown(runner, plan.namespace)
