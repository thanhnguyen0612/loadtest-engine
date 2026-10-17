"""k6 summary extraction, pass/fail Result, and Markdown/HTML report rendering."""

import html as _html
import json
import re
from dataclasses import dataclass

from loadtest.config import parse_cpu_m, parse_memory_mi
from loadtest.estimate import CostEstimate
from loadtest.plan import TestPlan

SUMMARY_BEGIN = "===LOADTEST_SUMMARY_BEGIN==="
SUMMARY_END = "===LOADTEST_SUMMARY_END==="

_EXPR = re.compile(r"^\s*(\S+?)\s*(<=|>=|==|<|>)\s*(\S+)\s*$")


class MissingResults(Exception):
    """k6 finished (or died) without printing a usable summary."""


@dataclass
class ThresholdCheck:
    name: str
    limit: str
    actual: str
    ok: bool


@dataclass
class Result:
    passed: bool
    smoke_passed: bool
    p50_ms: float
    p95_ms: float
    p99_ms: float
    rps: float
    error_rate: float
    checks: list[ThresholdCheck]
    peak_cpu_m: int | None
    peak_mem_mi: int | None
    cpu_limit_m: int
    mem_limit_mi: int


def extract_summary(logs: str) -> dict:
    start = logs.find(SUMMARY_BEGIN)
    end = logs.find(SUMMARY_END, start + len(SUMMARY_BEGIN)) if start != -1 else -1
    if start == -1 or end == -1:
        raise MissingResults("k6 summary markers not found in logs")
    try:
        data = json.loads(logs[start + len(SUMMARY_BEGIN):end])
    except json.JSONDecodeError as e:
        raise MissingResults(f"k6 summary is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise MissingResults("k6 summary is not a JSON object")
    return data


def _fmt(metric: str, value: float | None, raw: str = "") -> str:
    if value is None:
        return raw or "n/a"
    if metric == "http_req_duration":
        return f"{value:.0f} ms"
    if metric == "http_req_failed":
        return f"{value * 100:.2f}%"
    if metric == "http_reqs":
        return f"{value:.1f}/s"
    return f"{value:g}"


def _checks(summary: dict) -> list[ThresholdCheck]:
    checks = []
    for metric, data in summary.get("metrics", {}).items():
        for expr, outcome in (data.get("thresholds") or {}).items():
            m = _EXPR.match(expr)
            stat, op, limit = m.groups() if m else (expr, "", "")
            value = data.get("values", {}).get(stat)
            if metric == "http_req_duration":
                name = f"{stat.replace('(', '').replace(')', '')} latency"
                limit_txt = f"{op} {limit} ms"
            elif metric == "http_req_failed":
                name = "error rate"
                limit_txt = f"{op} {float(limit) * 100:g}%" if m else expr
            elif metric == "http_reqs" and stat == "rate":
                name = "request rate (platform cap)"
                limit_txt = f"{op} {limit}/s"
            else:
                name, limit_txt = f"{metric} {stat}", (f"{op} {limit}" if m else expr)
            if "ok" not in outcome:
                raise MissingResults(f"k6 summary threshold {metric} {expr} has no result")
            checks.append(ThresholdCheck(name, limit_txt, _fmt(metric, value), bool(outcome["ok"])))
    return checks


def build_result(summary: dict, plan: TestPlan, peak_cpu_m: int | None,
                 peak_mem_mi: int | None, smoke_passed: bool = True) -> Result:
    def values(metric: str, *keys: str) -> list:
        vals = summary.get("metrics", {}).get(metric, {}).get("values", {})
        missing = [k for k in keys if k not in vals]
        if missing:
            raise MissingResults(f"k6 summary missing metric {metric} ({', '.join(missing)})")
        return [vals[k] for k in keys]

    p50, p95, p99 = values("http_req_duration", "med", "p(95)", "p(99)")
    (rps,) = values("http_reqs", "rate")
    (error_rate,) = values("http_req_failed", "rate")
    checks = _checks(summary)
    if not checks:
        raise MissingResults("k6 summary had no thresholds")
    return Result(
        passed=smoke_passed and all(c.ok for c in checks),
        smoke_passed=smoke_passed,
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        rps=rps,
        error_rate=error_rate,
        checks=checks,
        peak_cpu_m=peak_cpu_m,
        peak_mem_mi=peak_mem_mi,
        cpu_limit_m=parse_cpu_m(plan.config.service.resources.cpu),
        mem_limit_mi=parse_memory_mi(plan.config.service.resources.memory),
    )


def _headline(result: Result | None, plan: TestPlan) -> str:
    svc = plan.config.service.name
    if result is None:
        return f"⚠️ ERROR — {svc}"
    if not result.smoke_passed:
        return f"❌ FAIL — {svc} (smoke test failed)"
    return f"✅ PASS — {svc}" if result.passed else f"❌ FAIL — {svc}"


def _plan_line(plan: TestPlan, est: CostEstimate) -> str:
    return (f"Target {plan.target_rps} rps for {plan.duration_s}s in `{plan.namespace}` "
            f"· est. cost ~${est.usd:.2f} ({est.vcpu:.2f} vCPU, {est.minutes:.0f} min)")


def _resource_rows(r: Result) -> list[tuple[str, str, str]]:
    cpu = "n/a" if r.peak_cpu_m is None else f"{r.peak_cpu_m}m"
    mem = "n/a" if r.peak_mem_mi is None else f"{r.peak_mem_mi}Mi"
    return [("Peak CPU", cpu, f"{r.cpu_limit_m}m"), ("Peak memory", mem, f"{r.mem_limit_mi}Mi")]


def _metric_rows(r: Result) -> list[tuple[str, str]]:
    return [("p50 latency", f"{r.p50_ms:.0f} ms"), ("p95 latency", f"{r.p95_ms:.0f} ms"),
            ("p99 latency", f"{r.p99_ms:.0f} ms"), ("Throughput", f"{r.rps:.1f} req/s"),
            ("Error rate", f"{r.error_rate * 100:.2f}%")]


def _icon(ok: bool) -> str:
    return "✅" if ok else "❌"


def render_markdown(result: Result | None, plan: TestPlan, estimate: CostEstimate,
                    error: str | None = None) -> str:
    out = [f"## {_headline(result, plan)}", "", _plan_line(plan, estimate), ""]
    if result is None:
        out += [f"**Error:** {error or 'no results collected'}", ""]
        return "\n".join(out)
    if not result.smoke_passed:
        out += ["The smoke test failed, so the load test did not count as a pass.", ""]
    out += ["### Metrics", "", "| Metric | Value |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in _metric_rows(result)]
    out += ["", "### Thresholds", "", "| Result | Check | Limit | Actual |", "|---|---|---|---|"]
    out += [f"| {_icon(c.ok)} | {c.name} | {c.limit} | {c.actual} |" for c in result.checks]
    out += ["", "### Resources", "", "| Resource | Peak | Limit |", "|---|---|---|"]
    out += [f"| {a} | {b} | {c} |" for a, b, c in _resource_rows(result)]
    out.append("")
    return "\n".join(out)


def _table(head: list[str], rows: list[tuple]) -> str:
    th = "".join(f"<th>{_html.escape(h)}</th>" for h in head)
    body = "".join(
        "<tr>" + "".join(f"<td>{_html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def render_html(result: Result | None, plan: TestPlan, estimate: CostEstimate,
                error: str | None = None) -> str:
    head = _html.escape(_headline(result, plan))
    parts = [f"<h1>{head}</h1>", f"<p>{_html.escape(_plan_line(plan, estimate))}</p>"]
    if result is None:
        parts.append(f"<p><strong>Error:</strong> {_html.escape(error or 'no results collected')}</p>")
    else:
        if not result.smoke_passed:
            parts.append("<p>The smoke test failed, so the load test did not count as a pass.</p>")
        parts += [
            "<h2>Metrics</h2>", _table(["Metric", "Value"], _metric_rows(result)),
            "<h2>Thresholds</h2>",
            _table(["Result", "Check", "Limit", "Actual"],
                   [(_icon(c.ok), c.name, c.limit, c.actual) for c in result.checks]),
            "<h2>Resources</h2>",
            _table(["Resource", "Peak", "Limit"], _resource_rows(result)),
        ]
    style = ("body{font-family:system-ui,sans-serif;max-width:48rem;margin:2rem auto;padding:0 1rem}"
             "table{border-collapse:collapse;margin-bottom:1rem}"
             "th,td{border:1px solid #bbb;padding:.3rem .7rem;text-align:left}")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{head}</title><style>{style}</style></head><body>{''.join(parts)}</body></html>")
