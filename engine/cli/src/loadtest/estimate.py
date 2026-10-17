"""Rough cost estimate shown before a run. Illustrative, not billing."""

from dataclasses import dataclass

from loadtest.config import parse_cpu_m
from loadtest.plan import TestPlan

USD_PER_VCPU_HOUR = 0.012  # illustrative GKE spot rate; not a quote
POSTGRES_VCPU = 0.5
STUB_VCPU = 0.25
K6_VCPU = 1.0
OVERHEAD_MIN = 5  # namespace setup, image pulls, teardown


@dataclass
class CostEstimate:
    usd: float
    vcpu: float
    minutes: float


def estimate_cost(plan: TestPlan) -> CostEstimate:
    cfg = plan.config
    vcpu = parse_cpu_m(cfg.service.resources.cpu) / 1000
    if cfg.dependencies.database:
        vcpu += POSTGRES_VCPU
    vcpu += STUB_VCPU * len(cfg.dependencies.services)
    vcpu += K6_VCPU
    minutes = plan.duration_s / 60 + OVERHEAD_MIN
    return CostEstimate(usd=vcpu * (minutes / 60) * USD_PER_VCPU_HOUR, vcpu=vcpu, minutes=minutes)
