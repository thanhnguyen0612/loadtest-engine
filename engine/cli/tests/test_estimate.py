from pathlib import Path

from loadtest.config import load_config
from loadtest.estimate import USD_PER_VCPU_HOUR, estimate_cost, format_usd
from loadtest.plan import build_plan

FIXTURE = Path(__file__).parent / "fixtures" / "valid" / "loadtest.yaml"


def plan(size="S"):
    base = load_config(FIXTURE)
    load = base.load.model_copy(update={"size": size, "target_rps": None, "duration_s": None})
    return build_plan(base.model_copy(update={"load": load}), "1", now=0)


def test_estimate_positive_and_scales_with_duration():
    s, l = estimate_cost(plan("S")), estimate_cost(plan("L"))
    assert s.usd > 0 and l.usd > s.usd and l.minutes > s.minutes


def test_estimate_components():
    e = estimate_cost(plan("S"))
    # fixture: 250m service + 0.5 postgres + 1 stub (0.25) + 1.0 k6
    assert e.vcpu == 0.25 + 0.5 + 0.25 + 1.0
    assert e.minutes == 1 + 5
    assert abs(e.usd - e.vcpu * (e.minutes / 60) * USD_PER_VCPU_HOUR) < 1e-9


def test_format_usd_matches_cli_and_report_wording():
    assert format_usd(0.004) == "<$0.01"
    assert format_usd(0.01) == "$0.01"
    assert format_usd(1.234) == "$1.23"
