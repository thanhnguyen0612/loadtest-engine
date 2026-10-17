import json
from pathlib import Path

import pytest

from loadtest.config import load_config
from loadtest.estimate import estimate_cost
from loadtest.plan import build_plan
from loadtest.report import (
    SUMMARY_BEGIN,
    SUMMARY_END,
    MissingResults,
    build_result,
    extract_summary,
    render_html,
    render_markdown,
)

FIX = Path(__file__).parent / "fixtures"
PASS = json.loads((FIX / "k6_summary_pass.json").read_text())
FAIL = json.loads((FIX / "k6_summary_fail.json").read_text())
plan = build_plan(load_config(FIX / "valid" / "loadtest.yaml"), "42", now=1000)
est = estimate_cost(plan)


def test_extract_summary_between_markers():
    assert extract_summary(f'noise\n{SUMMARY_BEGIN}\n{{"a":1}}\n{SUMMARY_END}\n') == {"a": 1}


def test_extract_summary_missing_markers_raises():
    with pytest.raises(MissingResults):
        extract_summary("k6 OOMKilled")


def test_extract_summary_invalid_json_raises():
    with pytest.raises(MissingResults):
        extract_summary(f"{SUMMARY_BEGIN}\nnot json\n{SUMMARY_END}")


def test_build_result_pass():
    r = build_result(PASS, plan, 180, 120)
    assert r.passed and r.smoke_passed
    assert r.p50_ms == pytest.approx(95.2)
    assert r.p95_ms == pytest.approx(212.4)
    assert r.rps == pytest.approx(49.8)
    assert r.error_rate == pytest.approx(0.002)
    assert all(c.ok for c in r.checks)
    assert (r.cpu_limit_m, r.mem_limit_mi) == (250, 256)
    assert (r.peak_cpu_m, r.peak_mem_mi) == (180, 120)


def test_build_result_fail_marks_failed_check():
    r = build_result(FAIL, plan, 900, 200)
    assert not r.passed
    bad = [c for c in r.checks if not c.ok]
    assert len(bad) == 1
    assert bad[0].actual == "812 ms"


def test_markdown_headline_and_table():
    md = render_markdown(build_result(FAIL, plan, 900, 200), plan, est)
    assert md.splitlines()[0].startswith("## ❌ FAIL")
    assert "p95" in md and "| ❌ |" in md
    assert "Peak CPU" in md and "250m" in md


def test_markdown_pass_and_na():
    md = render_markdown(build_result(PASS, plan, None, None), plan, est)
    assert md.splitlines()[0] == "## ✅ PASS — sample-api"
    assert "n/a" in md


def test_markdown_error_variant():
    md = render_markdown(None, plan, est, error="no results collected")
    assert md.splitlines()[0].startswith("## ⚠️ ERROR")
    assert "no results collected" in md


def test_markdown_smoke_failed_variant():
    r = build_result(PASS, plan, 1, 1, smoke_passed=False)
    md = render_markdown(r, plan, est)
    assert md.splitlines()[0].startswith("## ❌ FAIL")
    assert "smoke" in md.splitlines()[0].lower()
    assert not r.passed


def test_html_contains_same_numbers():
    r = build_result(FAIL, plan, 900, 200)
    html = render_html(r, plan, est)
    assert "FAIL" in html and "812 ms" in html and "250m" in html and "900m" in html
    assert "http://" not in html and "https://" not in html
    assert "<script src" not in html and "<link" not in html


def test_html_error_variant_escapes():
    html = render_html(None, plan, est, error="<b>boom</b>")
    assert "ERROR" in html and "&lt;b&gt;boom" in html and "<b>boom" not in html


def test_extract_summary_non_object_raises():
    with pytest.raises(MissingResults):
        extract_summary(f"{SUMMARY_BEGIN}\n[1]\n{SUMMARY_END}")


def test_build_result_empty_metrics_raises_missing_results():
    with pytest.raises(MissingResults, match="http_req_duration"):
        build_result({"metrics": {}}, plan, None, None)


def test_build_result_missing_value_key_raises_missing_results():
    broken = json.loads(json.dumps(PASS))
    del broken["metrics"]["http_req_duration"]["values"]["p(95)"]
    with pytest.raises(MissingResults, match="p\\(95\\)"):
        build_result(broken, plan, None, None)


def test_build_result_threshold_without_ok_raises_missing_results():
    broken = json.loads(json.dumps(PASS))
    broken["metrics"]["http_req_duration"]["thresholds"] = {"p(95)<500": {}}
    with pytest.raises(MissingResults):
        build_result(broken, plan, None, None)


def test_build_result_no_thresholds_raises_missing_results():
    bare = json.loads(json.dumps(PASS))
    for m in bare["metrics"].values():
        m.pop("thresholds", None)
    with pytest.raises(MissingResults, match="thresholds"):
        build_result(bare, plan, None, None)
