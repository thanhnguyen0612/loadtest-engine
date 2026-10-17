"""`loadtest` command line: validate, estimate, run, teardown.

Exit codes: 0 pass, 1 thresholds failed (incl. smoke), 2 config invalid, 3 platform error
(including any unexpected exception; set LOADTEST_DEBUG=1 for the traceback).
"""

import argparse
import os
import sys
import traceback
from pathlib import Path

import yaml

from loadtest import kube, orchestrator
from loadtest.config import ConfigError, load_config
from loadtest.estimate import estimate_cost, format_usd
from loadtest.plan import build_plan, namespace_name

PREVIEW_RUN_ID = "preview"  # validate/estimate never touch a cluster


def _load_plan(args):
    cfg = load_config(Path(args.config), args.image)
    return build_plan(cfg, PREVIEW_RUN_ID, now=0)


def _errors(errors: list[str], header: str = "config invalid:") -> int:
    print(header, file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return 2


def cmd_validate(args) -> int:
    plan = _load_plan(args)
    cfg = plan.config
    est = estimate_cost(plan)
    doc = {
        "service": {"name": cfg.service.name, "team": cfg.service.team,
                    "image": cfg.service.image, "port": cfg.service.port,
                    "resources": cfg.service.resources.model_dump()},
        "load": {
            "size": cfg.load.size,
            "target_rps": plan.target_rps,
            "duration_s": plan.duration_s,
            "stages": [{"duration_s": s.duration_s, "target_rps": s.target_rps}
                       for s in plan.stages],
            "endpoints": [f"{e.method} {e.path}" for e in cfg.load.endpoints],
        },
        "thresholds": cfg.thresholds.model_dump(),
        "dependencies": {
            "database": cfg.dependencies.database.engine if cfg.dependencies.database else None,
            "services": [d.name for d in cfg.dependencies.services],
        },
        "estimated_cost_usd": round(est.usd, 4),
    }
    print(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), end="")
    return 0


def cmd_estimate(args) -> int:
    est = estimate_cost(_load_plan(args))
    print(f"Estimated cost: {format_usd(est.usd)} ({est.vcpu:.1f} vCPU × {est.minutes:.0f} min, "
          "GKE spot list price, illustrative)")
    return 0


def cmd_run(args) -> int:
    scheduling = None
    if args.scheduling:
        try:
            scheduling = yaml.safe_load(Path(args.scheduling).read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            return _errors([f"cannot read scheduling file {args.scheduling}: {exc}"])
        if not isinstance(scheduling, dict):
            return _errors([f"scheduling file {args.scheduling} must be a mapping"])
    outcome = orchestrator.run_test(Path(args.config), args.image, args.run_id, Path(args.out),
                                    runner=kube.run, scheduling=scheduling)
    if outcome.exit_code == 2:
        print(outcome.markdown, file=sys.stderr)
    else:
        print(outcome.markdown)
    return outcome.exit_code


def cmd_teardown(args) -> int:
    if args.namespace:
        ns = args.namespace
    elif args.config and args.run_id:
        # Only service.name is needed; teardown must work even if the config is otherwise
        # invalid (e.g. no image), so don't run full validation.
        try:
            raw = yaml.safe_load(Path(args.config).read_text())
            name = raw["service"]["name"]
        except (OSError, yaml.YAMLError, TypeError, KeyError) as exc:
            return _errors([f"cannot read service.name from {args.config}: {exc}"])
        ns = namespace_name(str(name), args.run_id)
    else:
        return _errors(["teardown needs --namespace NS, or --config CONFIG and --run-id ID"],
                       "usage error:")
    if not ns.startswith("lt-"):
        return _errors([f"refusing to delete {ns!r}: test namespaces start with 'lt-'"],
                       "usage error:")
    orchestrator.teardown(kube.run, ns)
    print(f"deleted namespace {ns} (if it existed)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="loadtest", description="Ephemeral load tests.")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate config and print the resolved plan")
    v.add_argument("config")
    v.add_argument("--image")
    v.set_defaults(func=cmd_validate)

    e = sub.add_parser("estimate", help="print the estimated cost")
    e.add_argument("config")
    e.add_argument("--image")
    e.set_defaults(func=cmd_estimate)

    r = sub.add_parser("run", help="run the test end to end")
    r.add_argument("config")
    r.add_argument("--image", required=True)
    r.add_argument("--run-id", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--scheduling", help="YAML file with nodeSelector/tolerations")
    r.set_defaults(func=cmd_run)

    t = sub.add_parser("teardown", help="delete a test namespace")
    t.add_argument("--namespace")
    t.add_argument("--config")
    t.add_argument("--run-id")
    t.set_defaults(func=cmd_teardown)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        return _errors(exc.errors)
    except Exception as exc:  # noqa: BLE001 -- anything unexpected is a platform error (3)
        if os.environ.get("LOADTEST_DEBUG") == "1":
            traceback.print_exc()
        print(f"platform error: {orchestrator.describe_error(exc)}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
