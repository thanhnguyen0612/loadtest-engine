import json
from pathlib import Path

from loadtest import limits
from loadtest.config import load_config
from loadtest.k6job import k6_job
from loadtest.plan import build_plan
from loadtest.report import SUMMARY_BEGIN, SUMMARY_END

FIXTURE = Path(__file__).parent / "fixtures" / "valid" / "loadtest.yaml"
K6_DIR = Path(__file__).parents[2] / "k6"
plan = build_plan(load_config(FIXTURE), "42", now=0)


def pod(j):
    return j["spec"]["template"]["spec"]


def envmap(j):
    return {e["name"]: e["value"] for e in pod(j)["containers"][0]["env"]}


def test_k6_job_env_and_deadline():
    j = k6_job(plan, "load")
    env = envmap(j)
    assert env["BASE_URL"] == "http://sample-api:8080" and env["PHASE"] == "load"
    assert json.loads(env["STAGES"]) == [
        {"duration_s": 12, "target_rps": 50},
        {"duration_s": 36, "target_rps": 50},
        {"duration_s": 12, "target_rps": 0},
    ]
    assert env["P95_MS"] == "500" and env["ERROR_RATE"] == "0.01"
    assert json.loads(env["ENDPOINTS"]) == [
        {"method": "GET", "path": "/items", "body": None},
        {"method": "POST", "path": "/orders", "body": {"item_id": 1, "quantity": 1}},
    ]
    assert j["metadata"]["name"] == "k6-load"
    assert j["spec"]["activeDeadlineSeconds"] == 60 + 120 and j["spec"]["backoffLimit"] == 0


def test_k6_job_smoke_deadline_and_name():
    j = k6_job(plan, "smoke")
    assert j["metadata"]["name"] == "k6-smoke"
    assert j["metadata"]["namespace"] == plan.namespace
    assert j["spec"]["activeDeadlineSeconds"] == 15 + 60
    assert envmap(j)["PHASE"] == "smoke"


def test_k6_job_command_mount_and_resources():
    p = pod(k6_job(plan, "load"))
    c = p["containers"][0]
    assert c["command"] == ["k6", "run", "/scripts/runner.js"]
    assert p["restartPolicy"] == "Never"
    assert {"name": "scripts", "mountPath": "/scripts", "readOnly": True} in c["volumeMounts"]
    assert p["volumes"] == [{"name": "scripts", "configMap": {"name": "k6-scripts"}}]
    want = {"cpu": f"{limits.K6_CPU_M}m", "memory": f"{limits.K6_MEMORY_MI}Mi"}
    assert c["resources"] == {"requests": want, "limits": want}


def test_k6_job_security_context_restricted():
    p = pod(k6_job(plan, "load"))
    assert p["securityContext"]["runAsNonRoot"] is True
    sc = p["containers"][0]["securityContext"]
    assert sc["runAsNonRoot"] is True and sc["runAsUser"] == 12345
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]}
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}


def test_k6_job_image_is_platform_pinned():
    assert pod(k6_job(plan, "load"))["containers"][0]["image"] == limits.PLATFORM_IMAGES["k6"]


def test_k6_job_scheduling():
    assert "nodeSelector" not in pod(k6_job(plan, "load"))
    sched = {"nodeSelector": {"pool": "load"},
             "tolerations": [{"key": "load", "operator": "Exists"}]}
    p = pod(k6_job(plan, "load", sched))
    assert p["nodeSelector"] == {"pool": "load"}
    assert p["tolerations"] == sched["tolerations"]


def test_runner_prints_summary_markers():
    src = (K6_DIR / "runner.js").read_text()
    assert SUMMARY_BEGIN in src and SUMMARY_END in src


def test_k6_job_passes_platform_rps_cap():
    assert envmap(k6_job(plan, "load"))["MAX_RPS"] == str(limits.MAX_RPS)


def test_runner_aborts_load_phase_above_request_rate_cap():
    src = (K6_DIR / "runner.js").read_text()
    assert "__ENV.MAX_RPS" in src
    assert "http_reqs" in src and "abortOnFail: true" in src
    assert "rate<=${MAX_RPS}" in src


def test_runner_applies_request_rate_cap_in_smoke_phase_too():
    assert envmap(k6_job(plan, "smoke"))["MAX_RPS"] == str(limits.MAX_RPS)
    src = (K6_DIR / "runner.js").read_text()
    smoke = src[src.index("const smoke"):src.index("const load")]
    assert "http_reqs: [rateGuard]" in smoke
