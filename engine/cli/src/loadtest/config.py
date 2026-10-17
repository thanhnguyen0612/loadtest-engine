"""loadtest.yaml schema and validation. All problems are reported together."""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from loadtest import limits

DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
METHODS = ("GET", "POST", "PUT", "DELETE")


class ConfigError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Resources(_Model):
    cpu: str = "250m"
    memory: str = "256Mi"


class ServiceSpec(_Model):
    name: str
    team: str
    image: str | None = None
    port: int = 8080
    health_path: str = "/healthz"
    resources: Resources = Resources()


class Endpoint(_Model):
    method: Literal["GET", "POST", "PUT", "DELETE"]
    path: str
    body: dict | None = None


class LoadSpec(_Model):
    size: Literal["S", "M", "L"] | None = "S"
    target_rps: int | None = None
    duration_s: int | None = None
    endpoints: list[Endpoint]
    scenario: str | None = None


class Thresholds(_Model):
    p95_ms: int = 500
    error_rate: float = 0.01


class DatabaseDep(_Model):
    engine: Literal["postgres"]
    seed: str | None = None


class ServiceDep(_Model):
    name: str
    mode: Literal["stub"]
    latency_ms: int = 0
    stubs: str | None = None


class Dependencies(_Model):
    database: DatabaseDep | None = None
    services: list[ServiceDep] = []


class LoadTestConfig(_Model):
    service: ServiceSpec
    load: LoadSpec
    thresholds: Thresholds = Thresholds()
    dependencies: Dependencies = Dependencies()
    base_dir: Path


def parse_cpu_m(s: str) -> int:
    m = re.fullmatch(r"(\d+)m|(\d+(?:\.\d+)?)", str(s))
    if not m:
        raise ValueError(f"invalid cpu quantity {s!r} (use e.g. '250m' or '1')")
    return int(m.group(1)) if m.group(1) is not None else round(float(m.group(2)) * 1000)


def parse_memory_mi(s: str) -> int:
    m = re.fullmatch(r"(\d+)(Mi|Gi)", str(s))
    if not m:
        raise ValueError(f"invalid memory quantity {s!r} (use e.g. '256Mi' or '1Gi')")
    return int(m.group(1)) * (1024 if m.group(2) == "Gi" else 1)


def _normalize_endpoints(raw: dict, errors: list[str]) -> None:
    load = raw.get("load")
    if not isinstance(load, dict) or not isinstance(load.get("endpoints"), list):
        return
    out = []
    for item in load["endpoints"]:
        if isinstance(item, str):
            parts = item.split(None, 1)
            if len(parts) != 2:
                errors.append(f"load.endpoints: {item!r} must look like 'GET /path'")
                continue
            item = {"method": parts[0].upper(), "path": parts[1].strip()}
        out.append(item)
    load["endpoints"] = out


def _pydantic_errors(e: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in e.errors()]


def _check_name(label: str, value: str, errors: list[str]) -> None:
    if len(value) > 63 or not DNS_LABEL.match(value):
        errors.append(f"{label} {value!r} must be a DNS-1123 label (lowercase a-z, 0-9, '-')")


def _check_file(label: str, rel: str | None, base: Path, errors: list[str]) -> None:
    if rel is None:
        return
    target = (base / rel).resolve()
    if not target.is_relative_to(base.resolve()):
        errors.append(f"{label} {rel!r} must stay inside the config directory")
    elif not target.is_file():
        errors.append(f"{label} file not found: {rel}")


def _semantic_errors(cfg: LoadTestConfig) -> list[str]:
    errors: list[str] = []
    svc = cfg.service
    _check_name("service.name", svc.name, errors)
    _check_name("service.team", svc.team, errors)

    if svc.image is None:
        errors.append("service.image is required (or pass --image)")
    elif not any(svc.image.startswith(r + "/") for r in limits.ALLOWED_REGISTRIES):
        errors.append(
            f"service.image {svc.image!r}: registry not allowed; use one of "
            f"{', '.join(limits.ALLOWED_REGISTRIES)}")

    for label, fn, value, cap, unit in (
        ("cpu", parse_cpu_m, svc.resources.cpu, limits.MAX_CPU_M, "m"),
        ("memory", parse_memory_mi, svc.resources.memory, limits.MAX_MEMORY_MI, "Mi"),
    ):
        try:
            if fn(value) > cap:
                errors.append(f"service.resources.{label} {value!r} exceeds cap {cap}{unit}")
        except ValueError as exc:
            errors.append(f"service.resources.{label}: {exc}")

    load = cfg.load
    if not load.endpoints:
        errors.append("load.endpoints must not be empty")
    for ep in load.endpoints:
        if "://" in ep.path or ep.path.startswith("//"):
            errors.append(f"endpoint path {ep.path!r}: absolute URLs are not allowed, "
                          "use a path starting with '/'")
        elif not ep.path.startswith("/"):
            errors.append(f"endpoint path {ep.path!r} must start with '/'")
    if load.size is None and (load.target_rps is None or load.duration_s is None):
        errors.append("load: target_rps and duration_s must both be set when size is null")
    if load.target_rps is not None and not 1 <= load.target_rps <= limits.MAX_RPS:
        errors.append(f"load.target_rps {load.target_rps} must be between 1 and {limits.MAX_RPS}")
    if load.duration_s is not None and not 1 <= load.duration_s <= limits.MAX_DURATION_S:
        errors.append(
            f"load.duration_s {load.duration_s} must be between 1 and {limits.MAX_DURATION_S}")
    _check_file("load.scenario", load.scenario, cfg.base_dir, errors)

    db = cfg.dependencies.database
    if db:
        _check_file("dependencies.database.seed", db.seed, cfg.base_dir, errors)
    for dep in cfg.dependencies.services:
        _check_name("dependencies.services name", dep.name, errors)
        if not 0 <= dep.latency_ms <= limits.MAX_LATENCY_MS:
            errors.append(f"dependencies.services[{dep.name}].latency_ms {dep.latency_ms} "
                          f"must be between 0 and {limits.MAX_LATENCY_MS}")
        _check_file(f"dependencies.services[{dep.name}].stubs", dep.stubs, cfg.base_dir, errors)
    return errors


def load_config(path: Path, image_override: str | None = None) -> LoadTestConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ConfigError([f"cannot read {path}: {exc}"]) from exc
    except yaml.YAMLError as exc:
        raise ConfigError([f"invalid YAML in {path}: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise ConfigError([f"{path}: top level must be a mapping"])

    errors: list[str] = []
    if image_override and isinstance(raw.get("service"), dict):
        raw["service"]["image"] = image_override
    _normalize_endpoints(raw, errors)
    raw["base_dir"] = path.parent
    try:
        cfg = LoadTestConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(errors + _pydantic_errors(exc)) from exc
    errors += _semantic_errors(cfg)
    if errors:
        raise ConfigError(errors)
    return cfg
