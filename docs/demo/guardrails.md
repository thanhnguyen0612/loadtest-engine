# Guardrail checks on the local shared cluster

Recorded on 2026-10-17 against `kind-loadtest` (kind v0.33, Kubernetes v1.37.0, Calico
v3.32.2, metrics-server v0.9.0; one node with 4 CPU / 8 GB under Colima on macOS arm64).
Outputs are trimmed; commands are exactly as run from the repo root.

Bring-up and the end-to-end run:

```console
$ make up                 # registry, kind cluster, Calico, metrics-server, terraform baseline
==> cluster ready: context kind-loadtest, registry localhost:5001
$ kubectl -n loadtest-system get cronjob janitor
NAME      SCHEDULE      TIMEZONE   SUSPEND   ACTIVE   LAST SCHEDULE   AGE
janitor   */5 * * * *   <none>     False     0        <none>          5s
$ kubectl top nodes
NAME                     CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)
loadtest-control-plane   307m         7%       764Mi           9%
$ make image loadtest RUN_ID=pass-1; echo "exit=$?"
## ✅ PASS — sample-api
...
exit=0
$ kubectl get ns -l loadtest.platform/managed=true
NAME                   STATUS        AGE
lt-sample-api-pass-1   Terminating   107s
```

Full reports: [summary-pass.md](summary-pass.md), [summary-fail.md](summary-fail.md).

## 1. Admission policy: only approved images in test namespaces

The policy checks Pod CREATE and UPDATE and ephemeral containers (`kubectl debug`).
Registry entries ending in `/` are prefixes. Pinned platform images must match exactly,
optionally followed by `@sha256:<digest>`.

```console
$ kubectl create ns lt-probe --dry-run=client -o yaml \
    | kubectl label --local -f - loadtest.platform/managed=true -o yaml \
    | kubectl apply -f - \
  && kubectl -n lt-probe run bad --image=docker.io/library/nginx
namespace/lt-probe created
Error from server (Forbidden): pods "bad" is forbidden: ValidatingAdmissionPolicy 'loadtest-allowed-images' with binding 'loadtest-allowed-images' denied request: image(s) not allowed in load-test namespaces: docker.io/library/nginx; allowed: localhost:5001/, docker.io/library/postgres:16-alpine, docker.io/wiremock/wiremock:3.9.1, docker.io/grafana/k6:0.54.0

# a lookalike tag of a pinned platform image is rejected
$ kubectl -n lt-probe run evil --image=docker.io/library/postgres:16-alpine-evil
Error from server (Forbidden): pods "evil" is forbidden: ValidatingAdmissionPolicy 'loadtest-allowed-images' with binding 'loadtest-allowed-images' denied request: image(s) not allowed in load-test namespaces: docker.io/library/postgres:16-alpine-evil; allowed: localhost:5001/, docker.io/library/postgres:16-alpine, docker.io/wiremock/wiremock:3.9.1, docker.io/grafana/k6:0.54.0

# the exact pinned image, or the same image pinned by digest, is admitted
$ kubectl -n lt-probe run pinned --image=docker.io/library/postgres:16-alpine --dry-run=server -o name
pod/pinned
$ kubectl -n lt-probe run digest --image=docker.io/library/postgres:16-alpine@sha256:0000…0000 --dry-run=server -o name
pod/digest

# an ephemeral debug container with an unapproved image is rejected
$ kubectl -n lt-probe run own --image=localhost:5001/sample-api:dev --command -- sleep 3600
pod/own created
$ kubectl -n lt-probe debug own --image=docker.io/library/busybox -- true
Error from server (Forbidden): pods "own" is forbidden: ValidatingAdmissionPolicy 'loadtest-allowed-images' with binding 'loadtest-allowed-images' denied request: image(s) not allowed in load-test namespaces: docker.io/library/busybox; allowed: localhost:5001/, docker.io/library/postgres:16-alpine, docker.io/wiremock/wiremock:3.9.1, docker.io/grafana/k6:0.54.0

$ kubectl delete ns lt-probe
namespace "lt-probe" deleted
```

Normal runs are unaffected. The section 4 runs below both PASS with this policy in place.

## 2. Network isolation: no egress outside the test namespace

During a run (`make loadtest RUN_ID=net-1`):

```console
$ kubectl -n lt-sample-api-net-1 exec deploy/sample-api -- \
    python -c "import urllib.request;urllib.request.urlopen('http://example.com',timeout=3)"
Traceback (most recent call last):
TimeoutError: timed out
...
urllib.error.URLError: <urlopen error timed out>
command terminated with exit code 1

# in-namespace traffic still works (the WireMock stub):
$ kubectl -n lt-sample-api-net-1 exec deploy/sample-api -- \
    python -c "import urllib.request;print(urllib.request.urlopen('http://payments-api:8080/__admin/health',timeout=3).status)"
200
```

## 3. Janitor: expired namespaces are deleted even if the runner died

```console
$ kubectl create ns lt-expired-demo2 --dry-run=client -o yaml \
    | kubectl label --local -f - loadtest.platform/managed=true -o yaml \
    | kubectl annotate --local -f - loadtest.platform/expires-at=$(( $(date +%s) - 60 )) -o yaml \
    | kubectl apply -f -
namespace/lt-expired-demo2 created
$ kubectl get ns -l loadtest.platform/managed=true
NAME                  STATUS   AGE
lt-expired-demo2      Active   0s
lt-sample-api-net-1   Active   46s      # a running test: not expired, left alone
$ make janitor-now
job.batch/janitor-manual-1792216829 created
job.batch/janitor-manual-1792216829 condition met
deleted namespace lt-expired-demo2
$ kubectl get ns -l loadtest.platform/managed=true
NAME                  STATUS        AGE
lt-expired-demo2      Terminating   5s
lt-sample-api-net-1   Active        51s
```

## 4. Concurrency cap: a test waits for a free slot

The cap defaults to `MAX_CONCURRENT_TESTS = 3`. You can override it with
`LOADTEST_MAX_CONCURRENT_TESTS` (Makefile: `MAX_CONCURRENT=`). This laptop's single node
(4 CPU allocatable, 1.2 CPU already requested by system pods) has about **2.8 CPU free**.
One test needs **2 CPU** at peak (service 250m + Postgres 500m + WireMock 250m + k6
1000m), so only one test fits at a time. The demo therefore runs with a cap of 1, with
two runs started 3 s apart:

```console
$ make loadtest MAX_CONCURRENT=1 RUN_ID=cap-1 > cap-1.log 2>&1 &
$ sleep 3; make loadtest MAX_CONCURRENT=1 RUN_ID=cap-2 > cap-2.log 2>&1 &
$ wait

$ cat cap-2.log
LOADTEST_MAX_CONCURRENT_TESTS=1 .venv/bin/loadtest run services/sample-api/loadtest/loadtest.yaml --image localhost:5001/sample-api:dev --run-id cap-2 --out results/cap-2
1 tests running (max 1); waiting for a slot...
1 tests running (max 1); waiting for a slot...      # repeated every 15 s, 7 times
...
## ✅ PASS — sample-api
...
| ✅ | p95 latency | < 300 ms | 86 ms |

$ head -1 results/cap-1/summary.md
## ✅ PASS — sample-api

# managed namespaces over time (sampled every 10 s)
13:15:31  cap-1 started
13:15:34  cap-2 started   (waits)
13:15:37  lt-sample-api-cap-1:Active
13:17:18  lt-sample-api-cap-1:Terminating
13:17:28  lt-sample-api-cap-2:Active      # second run admitted once the first finished
13:18:59  cap-2 done; no managed namespaces left
```

With the default cap of 3 on this node, three environments are admitted at once, but
their k6 pods stay `Pending` for lack of CPU until the Job deadline. The run then
reports `## ⚠️ ERROR`, and the report lists the pod states. Set the cap to what the
cluster can actually hold.

## 5. Threshold failure: exit 1 and a FAIL report

A copy of the config dir with `p95_ms: 1`:

```console
$ cp -R services/sample-api/loadtest /tmp/fail-cfg && sed -i '' 's/p95_ms: 300/p95_ms: 1/' /tmp/fail-cfg/loadtest.yaml
$ make loadtest RUN_ID=fail-1 CONFIG=/tmp/fail-cfg/loadtest.yaml
## ❌ FAIL — sample-api
...
| ❌ | p95 latency | < 1 ms | 87 ms |
| ✅ | error rate | < 1% | 0.00% |
...
make: *** [loadtest] Error 1          # the CLI exited 1
```

## 6. Request-rate guard: a custom scenario above 500 req/s is aborted

The load shape caps **iterations**/s. A custom `scenario.js` that sends two requests per
iteration at `target_rps: 300` asks for 600 req/s. The platform `runner.js` adds a
`http_reqs` threshold `rate<=500` (`limits.MAX_RPS`) with `abortOnFail`, so k6 stops the run.

A copy of the config dir with `size: null`, `target_rps: 300`, `duration_s: 60`,
`scenario: scenario.js` and the service at `cpu: 1000m`:

```js
export default function () {
  http.get(`${__ENV.BASE_URL}/items?limit=1`);
  http.get(`${__ENV.BASE_URL}/items?limit=1`);
}
```

At the default 250m the service saturates at about 220 req/s (p95 2.6 s), so the guard
never trips and the run fails on p95 instead. 1000m is what the laptop can hold next to
Postgres, WireMock and k6 (2.75 CPU of ~2.8 free); at that size the service serves the load:

```console
$ make loadtest MAX_CONCURRENT=1 RUN_ID=rps-guard-2 CONFIG=/tmp/flood/loadtest.yaml
## ❌ FAIL — sample-api
...
| ❌ | request rate (platform cap) | <= 500/s | 505.1/s |
| ✅ | p95 latency | < 300 ms | 43 ms |
| ✅ | error rate | < 1% | 0.00% |
...
make: *** [loadtest] Error 1          # the CLI exited 1

# k6-summary.json: the run stopped at 38 s of the planned 60 s
http_reqs   count 19190, rate 505.1/s   {"rate<=500": {"ok": false}}
iterations  count 9589,  rate 252.4/s
state.testRunDurationMs  37990
```

A normal run reports the guard as a passing row:
`| ✅ | request rate (platform cap) | <= 500/s | 40.0/s |`.
