import json
import subprocess
import sys
from pathlib import Path

import yaml

from loadtest import kube
from loadtest.__main__ import main
from loadtest.report import SUMMARY_BEGIN, SUMMARY_END

FIX = Path(__file__).parent / "fixtures"
CONFIG = str(FIX / "valid" / "loadtest.yaml")
PASS = json.loads((FIX / "k6_summary_pass.json").read_text())
IMAGE = "localhost:5001/x:1"


class Recorder:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self, cmd, stdin=None):
        self.calls.append(cmd)
        line = " ".join(cmd)
        for prefix, resp in self.responses.items():
            if line.startswith(prefix):
                return resp
        return ""


def test_validate_prints_resolved_plan(capsys):
    assert main(["validate", CONFIG, "--image", IMAGE]) == 0
    plan = yaml.safe_load(capsys.readouterr().out)
    assert plan["service"]["name"] == "sample-api"
    assert plan["service"]["image"] == IMAGE
    assert (plan["load"]["size"], plan["load"]["target_rps"], plan["load"]["duration_s"]) == (
        "S", 50, 60)
    assert plan["load"]["stages"][0] == {"duration_s": 12, "target_rps": 50}
    assert plan["thresholds"] == {"p95_ms": 500, "error_rate": 0.01}
    assert plan["estimated_cost_usd"] > 0


def test_validate_invalid_config_exits_2_listing_errors(tmp_path, capsys):
    bad = tmp_path / "loadtest.yaml"
    bad.write_text("service: {name: ok, team: t}\n"
                   "load: {target_rps: 9999, size: null, duration_s: 10, "
                   "endpoints: ['GET http://x/y']}\n")
    assert main(["validate", str(bad), "--image", "docker.io/x:1"]) == 2
    err = capsys.readouterr().err
    assert "registry not allowed" in err
    assert "absolute URLs" in err
    assert "target_rps" in err


def test_estimate_prints_one_line(capsys):
    assert main(["estimate", CONFIG]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "Estimated cost: <$0.01 (2.0 vCPU × 6 min, GKE spot list price, illustrative)"


def test_estimate_rounds_to_cents_above_one_cent(tmp_path, capsys):
    cfg = tmp_path / "loadtest.yaml"
    cfg.write_text("service: {name: big, team: t, image: localhost:5001/x:1, "
                   "resources: {cpu: '2', memory: 1Gi}}\n"
                   "load: {size: L, endpoints: ['GET /']}\n")
    assert main(["estimate", str(cfg)]) == 0
    assert capsys.readouterr().out.strip() == (
        "Estimated cost: $0.01 (3.0 vCPU × 20 min, GKE spot list price, illustrative)")


def test_run_prints_markdown_and_exits_with_outcome(tmp_path, capsys, monkeypatch):
    rec = Recorder({
        "kubectl get namespaces": json.dumps({"items": []}),
        "kubectl get job": json.dumps({"status": {"succeeded": 1}}),
        "kubectl logs": f"{SUMMARY_BEGIN}\n{json.dumps(PASS)}\n{SUMMARY_END}\n",
    })
    monkeypatch.setattr(kube, "run", rec)
    sched = tmp_path / "sched.yaml"
    sched.write_text("nodeSelector: {pool: loadtest}\ntolerations: []\n")
    code = main(["run", CONFIG, "--image", IMAGE, "--run-id", "42", "--out",
                 str(tmp_path / "out"), "--scheduling", str(sched)])
    assert code == 0
    assert "PASS" in capsys.readouterr().out
    assert (tmp_path / "out" / "summary.md").exists()
    assert rec.calls[-1][:4] == ["kubectl", "delete", "namespace", "lt-sample-api-42"]


def test_run_bad_scheduling_file_exits_2(tmp_path, capsys, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(kube, "run", rec)
    code = main(["run", CONFIG, "--image", IMAGE, "--run-id", "42", "--out",
                 str(tmp_path / "out"), "--scheduling", str(tmp_path / "missing.yaml")])
    assert code == 2
    assert rec.calls == []
    assert "scheduling" in capsys.readouterr().err


def test_run_invalid_config_exits_2(tmp_path, capsys, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(kube, "run", rec)
    code = main(["run", CONFIG, "--image", "docker.io/x:1", "--run-id", "42", "--out",
                 str(tmp_path / "out")])
    assert code == 2
    assert rec.calls == []
    assert "registry not allowed" in capsys.readouterr().err


def test_teardown_by_namespace(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(kube, "run", rec)
    assert main(["teardown", "--namespace", "lt-sample-api-42"]) == 0
    assert rec.calls == [["kubectl", "delete", "namespace", "lt-sample-api-42",
                          "--ignore-not-found", "--wait=false"]]


def test_teardown_by_config_and_run_id(monkeypatch, tmp_path):
    rec = Recorder()
    monkeypatch.setattr(kube, "run", rec)
    # No image in the config: teardown must not need a fully valid config.
    cfg = tmp_path / "loadtest.yaml"
    cfg.write_text("service: {name: sample-api, team: orders}\nload: {endpoints: []}\n")
    assert main(["teardown", "--config", str(cfg), "--run-id", "42"]) == 0
    assert rec.calls[0][:4] == ["kubectl", "delete", "namespace", "lt-sample-api-42"]


def test_teardown_requires_namespace_or_config(monkeypatch, capsys):
    rec = Recorder()
    monkeypatch.setattr(kube, "run", rec)
    assert main(["teardown", "--config", CONFIG]) == 2
    assert main(["teardown"]) == 2
    assert rec.calls == []


def test_teardown_refuses_unmanaged_namespace(monkeypatch, capsys):
    rec = Recorder()
    monkeypatch.setattr(kube, "run", rec)
    assert main(["teardown", "--namespace", "kube-system"]) == 2
    assert rec.calls == []
    assert "lt-" in capsys.readouterr().err


def test_python_m_loadtest_runs():
    proc = subprocess.run([sys.executable, "-m", "loadtest", "estimate", CONFIG],
                          check=False, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("Estimated cost: ")


def test_gke_scheduling_example_reaches_the_k6_job(tmp_path, monkeypatch):
    example = Path(__file__).resolve().parents[2] / "chart" / "loadtest-env" / "values-gke.example.yaml"
    stdins = []

    def rec(cmd, stdin=None):
        if stdin:
            stdins.append(stdin)
        line = " ".join(cmd)
        if line.startswith("kubectl get namespaces"):
            return json.dumps({"items": []})
        if line.startswith("kubectl get job"):
            return json.dumps({"status": {"succeeded": 1}})
        if line.startswith("kubectl logs"):
            return f"{SUMMARY_BEGIN}\n{json.dumps(PASS)}\n{SUMMARY_END}\n"
        return ""

    monkeypatch.setattr(kube, "run", rec)
    code = main(["run", CONFIG, "--image", IMAGE, "--run-id", "42", "--out",
                 str(tmp_path / "out"), "--scheduling", str(example)])
    assert code == 0
    jobs = [d for s in stdins for d in yaml.safe_load_all(s) if d and d.get("kind") == "Job"]
    assert jobs, "no Job manifest was applied"
    pod = jobs[0]["spec"]["template"]["spec"]
    assert pod["nodeSelector"] == {"workload": "loadtest"}
    assert pod["tolerations"] == [{"key": "dedicated", "operator": "Equal", "value": "loadtest",
                                   "effect": "NoSchedule"}]
