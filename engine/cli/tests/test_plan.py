import re
from pathlib import Path

from loadtest import limits
from loadtest.config import load_config
from loadtest.plan import build_plan, namespace_expiry, namespace_name

FIXTURE = Path(__file__).parent / "fixtures" / "valid" / "loadtest.yaml"


def cfg(size="S", target_rps=None, duration_s=None):
    base = load_config(FIXTURE)
    load = base.load.model_copy(
        update={"size": size, "target_rps": target_rps, "duration_s": duration_s})
    return base.model_copy(update={"load": load})


def test_stages_ramp_hold_rampdown():
    p = build_plan(cfg(size="S"), "123", now=1000)
    assert [s.target_rps for s in p.stages] == [50, 50, 0]
    assert [s.duration_s for s in p.stages] == [12, 36, 12]  # 20% / 60% / 20% of 60s


def test_custom_rps_duration_used():
    p = build_plan(cfg(size=None, target_rps=200, duration_s=120), "1", now=0)
    assert (p.target_rps, p.duration_s) == (200, 120)
    assert [s.target_rps for s in p.stages] == [200, 200, 0]
    assert sum(s.duration_s for s in p.stages) == 120


def test_explicit_values_override_preset_field_by_field():
    p = build_plan(cfg(size="S", duration_s=120), "1", now=0)
    assert (p.target_rps, p.duration_s) == (50, 120)
    p = build_plan(cfg(size="S", target_rps=75), "1", now=0)
    assert (p.target_rps, p.duration_s) == (75, 60)


def test_stage_durations_always_sum_to_duration():
    for d in (1, 2, 7, 33, 901):
        p = build_plan(cfg(size=None, target_rps=10, duration_s=d), "1", now=0)
        assert sum(s.duration_s for s in p.stages) == d
        assert all(s.duration_s >= 0 for s in p.stages)


def test_expires_at_covers_deploy_smoke_load_and_buffer():
    # now + duration + smoke (15 s) + helm --wait (300 s) + TTL buffer (900 s)
    assert build_plan(cfg(size="S"), "1", now=1000).expires_at == 1000 + 60 + 15 + 300 + 900


def test_namespace_expiry_is_relative_to_start():
    assert namespace_expiry(5000, 120) == 5000 + 120 + 15 + 300 + 900
    assert limits.MAX_NAMESPACE_LIFETIME_S == namespace_expiry(0, limits.MAX_DURATION_S)


def test_plan_carries_run_id_and_namespace():
    p = build_plan(cfg(size="S"), "42", now=0)
    assert p.run_id == "42" and p.namespace == "lt-sample-api-42"


def test_namespace_name_unique_per_run_and_truncated():
    a, b = namespace_name("x" * 80, "111"), namespace_name("x" * 80, "222")
    assert a != b and len(a) <= 63 and re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", a)
    assert namespace_name("Sample_API", "42") == "lt-sample-api-42"


def test_namespace_name_long_run_id_keeps_last_12_chars():
    n = namespace_name("svc", "1234567890123456789")
    assert n == "lt-svc-890123456789"
