import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from loadtest import limits
from loadtest.config import load_config
from loadtest.plan import build_plan
from loadtest.values import render_values

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "engine" / "chart" / "loadtest-env"
K6_DIR = ROOT / "engine" / "k6"
FIXTURE = Path(__file__).parent / "fixtures" / "valid" / "loadtest.yaml"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

SCHEDULING = {
    "nodeSelector": {"pool": "loadtest"},
    "tolerations": [{"key": "loadtest", "operator": "Exists", "effect": "NoSchedule"}],
}


def values_for(cfg=None, scheduling=None):
    plan = build_plan(cfg or load_config(FIXTURE), "42", now=0)
    return render_values(plan, K6_DIR, scheduling)


def write_values(tmp_path, values):
    p = tmp_path / "values.json"
    p.write_text(json.dumps(values))
    return p


def helm(args):
    return subprocess.run(["helm", *args], capture_output=True, text=True, check=True).stdout


def render(tmp_path, values):
    out = helm(["template", "t", str(CHART), "-n", "ns", "-f", str(write_values(tmp_path, values))])
    return [d for d in yaml.safe_load_all(out) if d]


def by_kind(docs, kind):
    return [d for d in docs if d["kind"] == kind]


def pod_specs(docs):
    return [d["spec"]["template"]["spec"] for d in by_kind(docs, "Deployment")]


def test_chart_renders_expected_objects(tmp_path):
    docs = render(tmp_path, values_for())
    names = {(d["kind"], d["metadata"]["name"]) for d in docs}
    assert {
        ("ResourceQuota", "loadtest-quota"),
        ("LimitRange", "loadtest-defaults"),
        ("NetworkPolicy", "default-deny-all"),
        ("NetworkPolicy", "allow-same-namespace"),
        ("NetworkPolicy", "allow-dns"),
        ("Deployment", "postgres"),
        ("Service", "postgres"),
        ("Deployment", "payments-api"),
        ("Service", "payments-api"),
        ("ConfigMap", "payments-api-mappings"),
        ("Deployment", "sample-api"),
        ("Service", "sample-api"),
        ("ConfigMap", "k6-scripts"),
    } <= names


def test_quota_and_limitrange_use_values_and_caps(tmp_path):
    values = values_for()
    docs = render(tmp_path, values)
    (quota,) = by_kind(docs, "ResourceQuota")
    hard = quota["spec"]["hard"]
    assert hard["limits.cpu"] == values["quota"]["cpu"]
    assert hard["limits.memory"] == values["quota"]["memory"]
    assert hard["pods"] == str(values["quota"]["pods"])
    (lr,) = by_kind(docs, "LimitRange")
    (item,) = lr["spec"]["limits"]
    assert item["max"] == {"cpu": f"{limits.MAX_CPU_M}m", "memory": f"{limits.MAX_MEMORY_MI}Mi"}
    assert item["default"] == {"cpu": "250m", "memory": "256Mi"}


def test_every_pod_is_restricted(tmp_path):
    docs = render(tmp_path, values_for())
    specs = pod_specs(docs)
    assert len(specs) == 3
    for spec in specs:
        assert spec["securityContext"]["runAsNonRoot"] is True
        assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        for c in spec["containers"]:
            sc = c["securityContext"]
            assert sc["runAsNonRoot"] is True
            assert sc["allowPrivilegeEscalation"] is False
            assert sc["capabilities"] == {"drop": ["ALL"]}
            assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
            assert c["resources"]["limits"]["cpu"] and c["resources"]["limits"]["memory"]
            assert c["resources"]["requests"]["cpu"] and c["resources"]["requests"]["memory"]


def test_default_deny_covers_ingress_and_egress(tmp_path):
    docs = render(tmp_path, values_for())
    deny = next(d for d in by_kind(docs, "NetworkPolicy")
                if d["metadata"]["name"] == "default-deny-all")
    assert deny["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert deny["spec"]["podSelector"] == {}
    assert "ingress" not in deny["spec"] and "egress" not in deny["spec"]


def test_dns_and_same_namespace_policies(tmp_path):
    docs = render(tmp_path, values_for())
    pols = {d["metadata"]["name"]: d["spec"] for d in by_kind(docs, "NetworkPolicy")}
    dns = pols["allow-dns"]["egress"][0]
    (to,) = dns["to"]
    assert to["namespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": "kube-system"}
    assert to["podSelector"]["matchLabels"] == {"k8s-app": "kube-dns"}
    assert {(p["protocol"], p["port"]) for p in dns["ports"]} == {("UDP", 53), ("TCP", 53)}
    same = pols["allow-same-namespace"]
    assert same["policyTypes"] == ["Ingress", "Egress"]
    assert same["ingress"] == [{"from": [{"podSelector": {}}]}]
    assert same["egress"] == [{"to": [{"podSelector": {}}]}]


def test_no_postgres_when_database_disabled(tmp_path):
    values = values_for()
    values["database"] = {"enabled": False, "seedSql": ""}
    docs = render(tmp_path, values)
    assert not [d for d in docs if d["metadata"]["name"].startswith("postgres")]
    assert {"sample-api", "payments-api"} <= {d["metadata"]["name"]
                                               for d in by_kind(docs, "Deployment")}


def test_images_are_platform_pinned(tmp_path):
    values = values_for()
    docs = render(tmp_path, values)
    images = {c["image"] for s in pod_specs(docs) for c in s["containers"]}
    images.discard(values["service"]["image"])
    assert images
    assert images <= set(limits.PLATFORM_IMAGES.values())


def test_app_deployment_contract(tmp_path):
    values = values_for()
    docs = render(tmp_path, values)
    dep = next(d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "sample-api")
    assert dep["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"] == "sample-api"
    (c,) = dep["spec"]["template"]["spec"]["containers"]
    assert c["readinessProbe"]["httpGet"]["path"] == values["service"]["healthPath"]
    assert c["resources"]["limits"] == values["service"]["resources"]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env == values["service"]["env"]
    svc = next(d for d in by_kind(docs, "Service") if d["metadata"]["name"] == "sample-api")
    assert svc["spec"]["selector"]["app.kubernetes.io/name"] == "sample-api"
    assert svc["spec"]["ports"][0]["port"] == values["service"]["port"]


def test_platform_component_sizes_match_limits(tmp_path):
    docs = render(tmp_path, values_for())
    res = {d["metadata"]["name"]: d["spec"]["template"]["spec"]["containers"][0]["resources"]
           for d in by_kind(docs, "Deployment")}
    assert res["postgres"]["limits"] == {
        "cpu": f"{limits.POSTGRES_CPU_M}m", "memory": f"{limits.POSTGRES_MEMORY_MI}Mi"}
    assert res["payments-api"]["limits"] == {
        "cpu": f"{limits.STUB_CPU_M}m", "memory": f"{limits.STUB_MEMORY_MI}Mi"}


def test_scheduling_applied_to_every_deployment(tmp_path):
    docs = render(tmp_path, values_for(scheduling=SCHEDULING))
    for spec in pod_specs(docs):
        assert spec["nodeSelector"] == SCHEDULING["nodeSelector"]
        assert spec["tolerations"] == SCHEDULING["tolerations"]


def test_postgres_and_wiremock_mounts(tmp_path):
    values = values_for()
    docs = render(tmp_path, values)
    pg = next(d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "postgres")
    spec = pg["spec"]["template"]["spec"]
    assert spec["securityContext"]["runAsUser"] == 70
    assert spec["securityContext"]["fsGroup"] == 70
    c = spec["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["POSTGRES_USER"] == env["POSTGRES_PASSWORD"] == env["POSTGRES_DB"] == "loadtest"
    assert env["PGDATA"].startswith("/var/lib/postgresql/data/")
    mounts = {m["mountPath"] for m in c["volumeMounts"]}
    assert {"/var/lib/postgresql/data", "/docker-entrypoint-initdb.d"} <= mounts
    wm = next(d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "payments-api")
    mounts = {m["mountPath"] for m in wm["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]}
    assert {"/home/wiremock/mappings", "/home/wiremock/__files"} <= mounts
    cm = next(d for d in by_kind(docs, "ConfigMap") if d["metadata"]["name"] == "k6-scripts")
    assert set(cm["data"]) == {"runner.js", "endpoints.js", "scenario.js"}


def test_helm_lint_passes(tmp_path):
    out = subprocess.run(
        ["helm", "lint", str(CHART), "-f", str(write_values(tmp_path, values_for()))],
        check=False, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
