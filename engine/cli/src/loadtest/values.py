"""Render the Helm values for one test environment from a TestPlan."""

import copy
import json
import math
from pathlib import Path

from loadtest import limits
from loadtest.config import parse_cpu_m, parse_memory_mi
from loadtest.plan import TestPlan

QUOTA_HEADROOM = 1.2
K6_JOB_PODS = 2
BASE_PODS = 4  # service, postgres, and slack for helm hooks/restarts
STUB_PORT = 8080
DEFAULT_SCENARIO = "export { default } from './endpoints.js';\n"
DATABASE_URL = "postgresql://loadtest:loadtest@postgres:5432/loadtest"

DEFAULT_STUB_MAPPING: dict = {
    "request": {"urlPattern": ".*"},
    "response": {
        "status": 200,
        "jsonBody": {"ok": True},
        "headers": {"Content-Type": "application/json"},
    },
}


def apply_latency(mapping: dict, latency_ms: int) -> dict:
    """Copy of `mapping` with a fixed delay added unless it already sets one."""
    out = copy.deepcopy(mapping)
    if latency_ms > 0:
        out.setdefault("response", {}).setdefault("fixedDelayMilliseconds", latency_ms)
    return out


def _stub_mappings(base_dir: Path, dep) -> dict[str, str]:
    stub_dir = base_dir / dep.stubs if dep.stubs else base_dir / "stubs" / dep.name
    files = sorted(stub_dir.glob("*.json")) if stub_dir.is_dir() else []
    if not files:
        return {"default.json": json.dumps(apply_latency(DEFAULT_STUB_MAPPING, dep.latency_ms))}
    return {f.name: json.dumps(apply_latency(json.loads(f.read_text()), dep.latency_ms))
            for f in files}


def _env_name(name: str) -> str:
    return name.upper().replace("-", "_")


def render_values(plan: TestPlan, k6_dir: Path, scheduling: dict | None = None) -> dict:
    cfg = plan.config
    svc = cfg.service
    db = cfg.dependencies.database
    deps = cfg.dependencies.services

    env = {}
    if db:
        env["DATABASE_URL"] = DATABASE_URL
    for dep in deps:
        env[f"{_env_name(dep.name)}_URL"] = f"http://{dep.name}:{STUB_PORT}"

    cpu_m = parse_cpu_m(svc.resources.cpu)
    mem_mi = parse_memory_mi(svc.resources.memory)
    if db:
        cpu_m += limits.POSTGRES_CPU_M
        mem_mi += limits.POSTGRES_MEMORY_MI
    cpu_m += limits.STUB_CPU_M * len(deps) + limits.K6_CPU_M
    mem_mi += limits.STUB_MEMORY_MI * len(deps) + limits.K6_MEMORY_MI

    scenario = DEFAULT_SCENARIO
    if cfg.load.scenario:
        scenario = (cfg.base_dir / cfg.load.scenario).read_text()
    k6_dir = Path(k6_dir)

    return {
        "runId": plan.run_id,
        "images": dict(limits.PLATFORM_IMAGES),
        "service": {
            "name": svc.name,
            "image": svc.image,
            "port": svc.port,
            "healthPath": svc.health_path,
            "resources": {"cpu": svc.resources.cpu, "memory": svc.resources.memory},
            "env": env,
        },
        "database": {
            "enabled": db is not None,
            "seedSql": (cfg.base_dir / db.seed).read_text() if db and db.seed else "",
        },
        "stubs": [{"name": d.name, "mappings": _stub_mappings(cfg.base_dir, d)} for d in deps],
        "k6": {"files": {
            "runner.js": (k6_dir / "runner.js").read_text(),
            "endpoints.js": (k6_dir / "endpoints.js").read_text(),
            "scenario.js": scenario,
        }},
        "quota": {
            "cpu": f"{math.ceil(cpu_m * QUOTA_HEADROOM)}m",
            "memory": f"{math.ceil(mem_mi * QUOTA_HEADROOM)}Mi",
            "pods": BASE_PODS + len(deps) + K6_JOB_PODS,
        },
        "scheduling": scheduling if scheduling is not None
        else {"nodeSelector": {}, "tolerations": []},
    }
