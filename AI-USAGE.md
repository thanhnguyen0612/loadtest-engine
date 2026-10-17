# AI usage

## Tools

- **Claude Code** (CLI), with the **superpowers** plugin skills, mainly `brainstorming`, `writing-plans` and `subagent-driven-development` (test-first implementation with a review per task).
- TODO(candidate): list any other AI tools used (chat assistants, IDE completion, etc.) and what for.

## Workflow

The work went through four stages. Each one produced a file that the next stage worked from.

1. **Brainstorming → design spec.** Claude summarised the assignment PDF, then asked one question at a time (cloud, trigger, results, dependencies, guardrail worries). The answers became [`docs/superpowers/specs/2026-10-17-loadtest-engine-design.md`](docs/superpowers/specs/2026-10-17-loadtest-engine-design.md). Section 5 of the spec records the decisions I made; section 13 lists the calls Claude made, flagged for my review.
2. **Builder check.** Before planning, a separate subagent read the spec cold, as the person who would have to build it, and raised four open questions. Claude's answers are recorded in spec section 13.
3. **Implementation plan.** [`docs/superpowers/plans/2026-10-17-loadtest-engine.md`](docs/superpowers/plans/2026-10-17-loadtest-engine.md): 13 tasks, each with files, tests to write first, and commands to verify. It also lists global constraints (caps, pinned images, exit codes) and "review focus" failure cases, each tied to a named test.
4. **Subagent-driven execution.** For each task a controller session:
   - dispatched a **fresh implementer subagent** with only that task's brief and the global constraints;
   - then dispatched a separate **reviewer subagent** that checked the diff for spec compliance and code quality;
   - ran fix rounds with scoped re-reviews until the reviewer had no open findings;
   - kept a **progress ledger** (git-ignored) with a pre-flight scan of how tasks connect, and numbered **rulings** (R1–R18) for gaps the plan did not settle. Examples: R1, `loadtest teardown` takes `--config` and `--run-id` because the workflow does not know the namespace; R12, document that the service image needs a numeric non-root `USER` rather than inspect images; R14, make the concurrency cap overridable because a laptop holds only one test.
   - A final review covers the whole branch.

### Calls made by Claude during the build

These were not discussed with me up front; the controller recorded them in the spec or the ledger for review:

- k6 runs inside the cluster as a Job. The platform-owned `runner.js` controls the load options, so a custom script cannot get past the caps. A default scenario means teams need no JavaScript.
- Namespace per test with labels and an `expires-at` annotation; a janitor CronJob for runs that are killed.
- A ValidatingAdmissionPolicy that allows only the local registry or the exact pinned platform images.
- The concurrency cap counts namespaces in the cluster; GitHub concurrency groups only hold one pending run, so they cannot be the cap.
- The GKE design: dedicated project, private nodes, Dataplane V2, a spot node pool with a cluster-wide maximum, a least-privilege node service account, budget alerts, and WIF pinned to the org.

TODO(candidate): note which of these you changed, and anything you would have done differently.

## Representative prompts

From the session, verbatim or nearly so:

> /superpowers:brainstorming Please check the requirements described in this file and brainstorming about it. Senior DevOps Engineer - Vietnam - Sep 2026 - Take-home Assignment.pdf

> first of all please summary the requirements for me.

> i don't have GCP/AWS account. I know Terraform, have github account.

> use GCP

> trigger it from github actions with a config file

> enough resource to scale or not ? how do developers know which size they should scale, and the cost...also not allow to do anything in PROD.

> do you think it's aligned with the requirements ?

> approved, write the implementation plan

> subagent-driven, go ahead

The guardrail prompt (capacity, sizing, cost, prod) shaped most of the platform. It led to the in-cluster concurrency cap, the S/M/L presets with hard caps, the peak CPU/memory in the report, the cost estimate, and the separate-project / default-deny design.

## How the AI output was validated

| Check | What it covers |
|---|---|
| Test-first unit tests (pytest, 133 engine tests + 6 sample-service tests) | Each implementer had to show a failing run before writing code, then the passing run. Covers config validation, plan, values, reports, k6 job, orchestrator (with a fake runner), janitor. |
| Independent reviewer per task | Found real defects that the implementer's own tests passed. See below. |
| Drift tests | Tie constants in Terraform, the chart and the janitor to `limits.py`, so the caps and pinned images cannot diverge. |
| `helm lint --strict` + chart tests | Chart rendered with values from the real CLI. |
| `terraform fmt` / `validate`, tflint, trivy | Every module and env, including the GCP ones that were never applied. trivy: 0 HIGH/CRITICAL findings. |
| actionlint | All workflows. |
| End-to-end runs on kind | Real PASS and FAIL runs of the sample service. |
| Manual guardrail probes | Admission policy, network isolation, janitor, concurrency cap and threshold failure, recorded with output in [docs/demo/guardrails.md](docs/demo/guardrails.md). |

Defects the reviewers caught:

- A k6 summary with a missing metric raised `KeyError`, and a summary with zero thresholds counted as PASS.
- Two tasks disagreed on whether `stubs` is a file or a directory, so the sample config would silently have used the default stub.
- The regional node pool's autoscaling limit was per zone, which would have tripled the node ceiling.
- The admission policy matched image prefixes, so a look-alike tag such as `postgres:16-alpine-evil` was allowed. It now requires an exact match (or a digest), and also covers updates and ephemeral containers.

Known limitations that remain are listed in [WRITEUP.md](WRITEUP.md#5-known-limitations).

## My own review

TODO(candidate): what you reviewed by hand, what you changed by hand, and roughly how much time you spent.
