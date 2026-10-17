import json
from pathlib import Path

from loadtest.config import load_config, parse_cpu_m, parse_memory_mi
from loadtest.plan import build_plan
from loadtest.values import DEFAULT_STUB_MAPPING, apply_latency, render_values

FIXTURE = Path(__file__).parent / "fixtures" / "valid" / "loadtest.yaml"


def plan_from_fixture(cfg=None):
    return build_plan(cfg or load_config(FIXTURE), "42", now=0)


def k6_dir(tmp_path):
    d = tmp_path / "k6"
    d.mkdir()
    (d / "runner.js").write_text("// runner")
    (d / "endpoints.js").write_text("// endpoints")
    return d


def test_apply_latency_sets_delay_when_absent():
    m = {"response": {"status": 200}}
    assert apply_latency(m, 80)["response"]["fixedDelayMilliseconds"] == 80
    assert "fixedDelayMilliseconds" not in m["response"]  # input untouched


def test_apply_latency_keeps_explicit_delay():
    m = {"response": {"fixedDelayMilliseconds": 5}}
    assert apply_latency(m, 80)["response"]["fixedDelayMilliseconds"] == 5


def test_apply_latency_zero_adds_nothing():
    assert "fixedDelayMilliseconds" not in apply_latency({"response": {}}, 0)["response"]


def test_env_contract(tmp_path):
    v = render_values(plan_from_fixture(), k6_dir(tmp_path))
    assert v["service"]["env"]["PAYMENTS_API_URL"] == "http://payments-api:8080"
    assert v["service"]["env"]["DATABASE_URL"].startswith(
        "postgresql://loadtest:loadtest@postgres:5432/")
    assert v["database"]["enabled"] is True
    assert v["database"]["seedSql"] == (FIXTURE.parent / "seed.sql").read_text()


def test_stub_mappings_loaded_with_latency(tmp_path):
    v = render_values(plan_from_fixture(), k6_dir(tmp_path))
    (stub,) = v["stubs"]
    assert stub["name"] == "payments-api"
    mapping = json.loads(stub["mappings"]["charge.json"])
    assert mapping["request"]["urlPath"] == "/charge"
    assert mapping["response"]["fixedDelayMilliseconds"] == 80


def test_default_stub_mapping_when_none_provided(tmp_path):
    base = load_config(FIXTURE)
    dep = base.dependencies.services[0].model_copy(update={"name": "ledger"})
    cfg = base.model_copy(update={"dependencies": base.dependencies.model_copy(
        update={"services": [dep]})})
    v = render_values(plan_from_fixture(cfg), k6_dir(tmp_path))
    mappings = v["stubs"][0]["mappings"]
    assert list(mappings) == ["default.json"]
    m = json.loads(mappings["default.json"])
    assert m["request"]["urlPattern"] == ".*"
    assert m["response"]["jsonBody"] == DEFAULT_STUB_MAPPING["response"]["jsonBody"]
    assert m["response"]["fixedDelayMilliseconds"] == 80
    assert "fixedDelayMilliseconds" not in DEFAULT_STUB_MAPPING["response"]


def test_quota_covers_all_components(tmp_path):
    v = render_values(plan_from_fixture(), k6_dir(tmp_path))
    needed_cpu = 250 + 500 + 250 + 1000
    needed_mem = 256 + 512 + 256 + 1024
    assert parse_cpu_m(v["quota"]["cpu"]) >= needed_cpu
    assert parse_memory_mi(v["quota"]["memory"]) >= needed_mem
    assert v["quota"]["pods"] == 4 + 1 + 2


def test_custom_scenario_included(tmp_path):
    base = load_config(FIXTURE)
    (tmp_path / "scn.js").write_text("export default function () {}")
    (tmp_path / "seed.sql").write_text("select 1;")
    cfg = base.model_copy(update={
        "base_dir": tmp_path,
        "load": base.load.model_copy(update={"scenario": "scn.js"})})
    v = render_values(plan_from_fixture(cfg), k6_dir(tmp_path))
    assert v["k6"]["files"]["scenario.js"] == "export default function () {}"


def test_default_scenario_reexports_endpoints(tmp_path):
    v = render_values(plan_from_fixture(), k6_dir(tmp_path))
    assert v["k6"]["files"]["scenario.js"].strip() == "export { default } from './endpoints.js';"
    assert set(v["k6"]["files"]) == {"runner.js", "endpoints.js", "scenario.js"}


def test_scheduling_default_and_passthrough(tmp_path):
    d = k6_dir(tmp_path)
    assert render_values(plan_from_fixture(), d)["scheduling"] == {
        "nodeSelector": {}, "tolerations": []}
    sched = {"nodeSelector": {"pool": "loadtest"}, "tolerations": [{"key": "lt"}]}
    assert render_values(plan_from_fixture(), d, sched)["scheduling"] == sched


def test_top_level_keys(tmp_path):
    v = render_values(plan_from_fixture(), k6_dir(tmp_path))
    assert set(v) == {"runId", "images", "service", "database", "stubs", "k6", "quota",
                      "scheduling"}
    json.dumps(v)  # must be serialisable for helm
