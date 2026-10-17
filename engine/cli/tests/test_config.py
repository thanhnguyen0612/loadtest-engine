from pathlib import Path

import pytest

from loadtest.config import (
    ConfigError,
    Endpoint,
    load_config,
    parse_cpu_m,
    parse_memory_mi,
)

FIXTURES = Path(__file__).parent / "fixtures"

BASE = """
service: {{name: svc, team: t, image: "{image}"{extra}}}
load: {{endpoints: {endpoints}{loadextra}}}
{tail}
"""


def write(tmp_path, image="localhost:5001/x:1", endpoints='["GET /a"]', extra="",
          loadextra="", tail=""):
    p = tmp_path / "loadtest.yaml"
    p.write_text(BASE.format(image=image, endpoints=endpoints, extra=extra,
                             loadextra=loadextra, tail=tail))
    return p


def test_load_valid_fixture():
    cfg = load_config(FIXTURES / "valid" / "loadtest.yaml")
    assert cfg.service.name == "sample-api" and cfg.service.team == "orders"
    assert cfg.load.endpoints[0] == Endpoint(method="GET", path="/items")
    assert cfg.dependencies.database.seed == "seed.sql"


def test_endpoint_string_form_parsed(tmp_path):
    cfg = load_config(write(tmp_path, endpoints='["POST /orders"]'))
    assert cfg.load.endpoints[0] == Endpoint(method="POST", path="/orders")


def test_image_override_wins():
    cfg = load_config(FIXTURES / "valid" / "loadtest.yaml", image_override="localhost:5001/x:1")
    assert cfg.service.image == "localhost:5001/x:1"


def test_missing_image_rejected(tmp_path):
    p = tmp_path / "loadtest.yaml"
    p.write_text("service: {name: svc, team: t}\nload: {endpoints: ['GET /a']}\n")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "image" in " ".join(e.value.errors)


def test_validate_reports_all_errors_together(tmp_path):
    p = write(tmp_path, image="docker.io/evil/x:1", endpoints='["GET http://prod.internal/x"]',
              loadextra=", size: null, target_rps: 10000, duration_s: 60")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    msgs = " ".join(e.value.errors)
    assert "absolute" in msgs and "registry" in msgs and "500" in msgs
    assert len(e.value.errors) >= 3


def test_duration_over_cap_rejected(tmp_path):
    p = write(tmp_path, loadextra=", size: null, target_rps: 10, duration_s: 3600")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "900" in " ".join(e.value.errors)


def test_custom_load_requires_both_fields(tmp_path):
    p = write(tmp_path, loadextra=", size: null, target_rps: 10")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "duration_s" in " ".join(e.value.errors)


def test_resources_over_cap_rejected(tmp_path):
    p = write(tmp_path, extra=', resources: {cpu: "4"}')
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "cpu" in " ".join(e.value.errors)


def test_unknown_field_rejected(tmp_path):
    p = write(tmp_path, tail="env: {SECRET: x}")
    with pytest.raises(ConfigError):
        load_config(p)


def test_seed_file_must_exist(tmp_path):
    p = write(tmp_path, tail="dependencies: {database: {engine: postgres, seed: missing.sql}}")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "missing.sql" in " ".join(e.value.errors)


def test_latency_over_cap_rejected(tmp_path):
    p = write(tmp_path, tail="dependencies: {services: [{name: dep, mode: stub, latency_ms: 6000}]}")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "5000" in " ".join(e.value.errors)


def test_bad_names_rejected(tmp_path):
    p = tmp_path / "loadtest.yaml"
    p.write_text("service: {name: Bad_Name, team: t, image: 'localhost:5001/x:1'}\n"
                 "load: {endpoints: ['GET /a']}\n")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "DNS-1123" in " ".join(e.value.errors)


def test_parse_cpu_and_memory():
    assert parse_cpu_m("1") == 1000 and parse_memory_mi("1Gi") == 1024
    assert parse_cpu_m("250m") == 250 and parse_memory_mi("256Mi") == 256


def _stubs_tail(value):
    return ("dependencies: {services: [{name: dep, mode: stub, stubs: " + value + "}]}")


def test_stubs_directory_accepted(tmp_path):
    (tmp_path / "stubs" / "dep").mkdir(parents=True)
    cfg = load_config(write(tmp_path, tail=_stubs_tail("stubs/dep")))
    assert cfg.dependencies.services[0].stubs == "stubs/dep"


def test_stubs_pointing_at_file_rejected(tmp_path):
    (tmp_path / "one.json").write_text("{}")
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, tail=_stubs_tail("one.json")))
    assert "one.json" in " ".join(e.value.errors)


def test_stubs_missing_directory_rejected(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_config(write(tmp_path, tail=_stubs_tail("nope")))
    assert "nope" in " ".join(e.value.errors)
