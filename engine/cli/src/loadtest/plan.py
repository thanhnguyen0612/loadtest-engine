"""Resolved test plan: effective load shape, stages, namespace name and expiry."""

import re
from dataclasses import dataclass

from loadtest import limits
from loadtest.config import LoadTestConfig

RAMP_FRACTION = 0.2


@dataclass(frozen=True)
class Stage:
    duration_s: int
    target_rps: int


@dataclass(frozen=True)
class TestPlan:
    __test__ = False  # not a pytest test class

    config: LoadTestConfig
    run_id: str
    namespace: str
    target_rps: int
    duration_s: int
    stages: list[Stage]
    expires_at: int


def _sanitize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def namespace_name(service: str, run_id: str) -> str:
    """`lt-{service}-{run_id}`, a DNS-1123 label of at most 63 chars; run_id kept whole."""
    rid = _sanitize(run_id[-12:])
    svc = _sanitize(service)[: 63 - len("lt--") - len(rid)].strip("-")
    return "-".join(p for p in ("lt", svc, rid) if p)


def namespace_expiry(start: int, duration_s: int) -> int:
    """`expires-at` for a namespace created at `start`: deploy + smoke + load + buffer.

    The buffer covers the k6 Job deadlines, reporting and teardown.
    """
    return (start + limits.PROVISION_TIMEOUT_S + limits.SMOKE_DURATION_S + duration_s
            + limits.TTL_BUFFER_S)


def build_plan(config: LoadTestConfig, run_id: str, now: int) -> TestPlan:
    load = config.load
    preset_rps, preset_dur = limits.PRESETS[load.size] if load.size else (None, None)
    rps = load.target_rps if load.target_rps is not None else preset_rps
    duration = load.duration_s if load.duration_s is not None else preset_dur
    ramp = round(duration * RAMP_FRACTION)
    stages = [
        Stage(ramp, rps),
        Stage(duration - 2 * ramp, rps),  # hold absorbs the rounding remainder
        Stage(ramp, 0),
    ]
    return TestPlan(
        config=config,
        run_id=run_id,
        namespace=namespace_name(config.service.name, run_id),
        target_rps=rps,
        duration_s=duration,
        stages=stages,
        expires_at=namespace_expiry(now, duration),
    )
