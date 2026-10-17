"""Builds the k6 Job manifest for the smoke and load phases."""

import json
from typing import Literal

from loadtest import limits
from loadtest.plan import TestPlan

SMOKE_DURATION_S = 15
DEADLINE_BUFFER_S = {"smoke": 60, "load": 120}
K6_UID = 12345  # the grafana/k6 image user


def k6_job(plan: TestPlan, phase: Literal["smoke", "load"],
           scheduling: dict | None = None) -> dict:
    cfg = plan.config
    svc = cfg.service
    run_s = SMOKE_DURATION_S if phase == "smoke" else plan.duration_s
    env = {
        "BASE_URL": f"http://{svc.name}:{svc.port}",
        "PHASE": phase,
        "STAGES": json.dumps([{"duration_s": s.duration_s, "target_rps": s.target_rps}
                              for s in plan.stages]),
        "P95_MS": str(cfg.thresholds.p95_ms),
        "ERROR_RATE": str(cfg.thresholds.error_rate),
        # Request-rate guard: aborts a custom scenario that sends more than the platform cap.
        "MAX_RPS": str(limits.MAX_RPS),
        "ENDPOINTS": json.dumps([{"method": e.method, "path": e.path, "body": e.body}
                                 for e in cfg.load.endpoints]),
    }
    resources = {"cpu": f"{limits.K6_CPU_M}m", "memory": f"{limits.K6_MEMORY_MI}Mi"}
    pod: dict = {
        "restartPolicy": "Never",
        "securityContext": {"runAsNonRoot": True},
        "containers": [{
            "name": "k6",
            "image": limits.PLATFORM_IMAGES["k6"],
            "command": ["k6", "run", "/scripts/runner.js"],
            "env": [{"name": k, "value": v} for k, v in env.items()],
            "resources": {"requests": dict(resources), "limits": dict(resources)},
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": K6_UID,
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "volumeMounts": [{"name": "scripts", "mountPath": "/scripts", "readOnly": True}],
        }],
        "volumes": [{"name": "scripts", "configMap": {"name": "k6-scripts"}}],
    }
    if scheduling:
        if scheduling.get("nodeSelector"):
            pod["nodeSelector"] = scheduling["nodeSelector"]
        if scheduling.get("tolerations"):
            pod["tolerations"] = scheduling["tolerations"]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": f"k6-{phase}", "namespace": plan.namespace},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": run_s + DEADLINE_BUFFER_S[phase],
            "template": {"spec": pod},
        },
    }
