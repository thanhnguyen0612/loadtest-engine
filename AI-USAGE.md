# AI usage

## Tools

- **Claude Code** (CLI), with the **superpowers** plugin skills, mainly `brainstorming`, `writing-plans` and `subagent-driven-development` (test-first implementation with a review per task).

## Workflow

### Setup and configuration

- **Agent:** Claude Code in the terminal, run from an empty working directory with the assignment PDF in it. I ran it in auto mode, so the agent asked me before anything outward-facing or destructive. Examples: installing tools with Homebrew, creating the GitHub repository, pushing. Auto mode blocked the one force-push, and I ran that myself.
- **Process plugin:** the `superpowers` skills, which turn a session into a fixed process: brainstorm → written design → plan → per-task implementation with review. Nothing was generated before I approved the design and then the plan.
- **Roles and models:** one *controller* session coordinated and never wrote product code itself. It dispatched short-lived subagents:
  - implementers: a mid-tier model for well-specified tasks, the most capable model for integration-heavy ones (the orchestrator, the kind end-to-end task, the workflows, the docs);
  - reviewers: a separate agent per task, never the implementer;
  - a cold-reader "builder check" on the design;
  - a final whole-branch reviewer on the most capable model.
- **Context control:** each subagent got a short brief file with only its task, the project-wide constraints and the report contract. It did not get the conversation history. Implementers wrote full reports to files and returned a few lines. Reviewers received the diff as a file.
- **Persistent state:** a git-ignored ledger recorded every dispatch, review verdict, deferred minor finding and ruling, so work could resume after an interruption without redoing tasks. One subagent was cut off by a network error mid-task and was resumed from its uncommitted changes.
- **Local environment the agent set up (with my approval):** Python 3.12, Docker via Colima, kind, kubectl, Helm, Terraform, tflint, trivy, actionlint and the GitHub CLI.

### Stages

The work went through four stages. Each one produced a file that the next stage worked from.

1. **Brainstorming → design spec.** Claude summarised the assignment PDF, then asked one question at a time (cloud, trigger, results, dependencies, guardrail worries). The answers became [`docs/superpowers/specs/2026-10-17-loadtest-engine-design.md`](docs/superpowers/specs/2026-10-17-loadtest-engine-design.md). Section 5 of the spec records the decisions I made; section 13 lists the calls Claude made, flagged for my review.
2. **Builder check.** Before planning, a separate subagent read the spec cold, as the person who would have to build it, and raised four open questions. Claude's answers are recorded in spec section 13.
3. **Implementation plan.** [`docs/superpowers/plans/2026-10-17-loadtest-engine.md`](docs/superpowers/plans/2026-10-17-loadtest-engine.md): 13 tasks, each with files, tests to write first, and commands to verify. It also lists global constraints (caps, pinned images, exit codes) and "review focus" failure cases, each tied to a named test.
4. **Subagent-driven execution.** For each task a controller session:
   - dispatched a **fresh implementer subagent** with only that task's brief and the global constraints;
   - then dispatched a separate **reviewer subagent** that checked the diff for spec compliance and code quality;
   - ran fix rounds with scoped re-reviews until the reviewer had no open findings;
   - kept a **progress ledger** (git-ignored) with a pre-flight scan of how tasks connect, and numbered **rulings** (R1–R21) for gaps the plan did not settle. Examples: R1, `loadtest teardown` takes `--config` and `--run-id` because the workflow does not know the namespace; R12, document that the service image needs a numeric non-root `USER` rather than inspect images; R14, make the concurrency cap overridable because a laptop holds only one test.
   - A final whole-branch review ran after all tasks, on the most capable model. It found no critical issues and six important ones, all fixed in a single fix wave and re-reviewed: the janitor could delete a test still waiting for capacity; unexpected errors exited 1 instead of 3; the reusable workflow only worked from this repo; on GKE the keyless CI login was not limited to the platform workflow; the runner image was unpinned. The sixth was my own submission items.

### Where I stayed in the loop

| Point | What I did |
|---|---|
| Requirements | Read Claude's summary against the PDF; corrected one misreading. |
| Design questions | Answered them one by one: cloud, trigger, results, dependencies, my guardrail worries. Asked whether the flow met the requirements. |
| Design spec | Approved it after the builder check's open questions were settled. |
| Plan | Approved it and chose subagent-driven execution. |
| During the build | Approved tool installs and the public repository; asked for status summaries; set the commit-message rule. |
| Outward actions | Repository creation and pushes went through me; I ran the force-push myself. |

### Calls made by Claude during the build

These were not discussed with me up front; the controller recorded them in the spec or the ledger for review:

- k6 runs inside the cluster as a Job. The platform-owned `runner.js` controls the load options (iterations/s), plus a 500 req/s abort guard for custom scripts that send several requests per iteration (added after review, ruling R19). A default scenario means teams need no JavaScript.
- Namespace per test with labels and an `expires-at` annotation; a janitor CronJob for runs that are killed.
- A ValidatingAdmissionPolicy that allows only the local registry or the exact pinned platform images.
- The concurrency cap counts namespaces in the cluster; GitHub concurrency groups only hold one pending run, so they cannot be the cap.
- The GKE design: dedicated project, private nodes, Dataplane V2, a spot node pool with a cluster-wide maximum, a least-privilege node service account, budget alerts, and WIF pinned to the org.

I read these calls in the spec and the progress reports and kept them; I did not overrule any of them. The decisions that changed direction were mine: GCP over AWS, Python for the glue and sample service, a public repository (GitHub's free runners give public repos 4 vCPU, enough for one test), and plain commit messages without AI attribution.

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
| Test-first unit tests (pytest, 147 engine tests + 6 sample-service tests) | Each implementer had to show a failing run before writing code, then the passing run. Covers config validation, plan, values, reports, k6 job, orchestrator (with a fake runner), janitor. |
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

- **Before any code:** I had Claude summarise the requirements and checked the summary against the PDF, corrected a misreading (the "no clarifying questions" rule applies to me and the company, not to the AI), asked whether the proposed developer flow actually met the requirements, and approved the design spec and then the plan before implementation started.
- **During the build:** I followed progress task by task and made the calls that left my machine: installing the tools, creating the public GitHub repository, and pushing. I ran the history rewrite's force-push myself.
- **By hand:** I wrote no code by hand. All code, tests and docs were generated and then checked by the separate reviewers and by the validation steps above.
- **Time:** about four hours of working session on one day, including the end-to-end runs on my laptop and on GitHub Actions.
