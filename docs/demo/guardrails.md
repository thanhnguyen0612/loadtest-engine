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

```console
$ kubectl create ns lt-probe --dry-run=client -o yaml \
    | kubectl label --local -f - loadtest.platform/managed=true -o yaml \
    | kubectl apply -f - \
  && kubectl -n lt-probe run bad --image=docker.io/library/nginx
namespace/lt-probe created
Error from server (Forbidden): pods "bad" is forbidden: ValidatingAdmissionPolicy 'loadtest-allowed-images' with binding 'loadtest-allowed-images' denied request: image(s) not allowed in load-test namespaces: docker.io/library/nginx; allowed prefixes: localhost:5001/, docker.io/library/postgres:16-alpine, docker.io/wiremock/wiremock:3.9.1, docker.io/grafana/k6:0.54.0

$ kubectl -n lt-probe run good --image=docker.io/grafana/k6:0.54.0 --dry-run=server -o name
pod/good

$ kubectl delete ns lt-probe
namespace "lt-probe" deleted
```

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

## 4. Concurrency cap: the 4th test waits for a slot (`MAX_CONCURRENT_TESTS = 3`)

Four runs started 3 s apart:

```console
$ for i in 1 2 3 4; do make loadtest RUN_ID=conc-$i > conc-$i.log 2>&1 & sleep 3; done; wait

$ cat conc-4.log
.venv/bin/loadtest run services/sample-api/loadtest/loadtest.yaml --image localhost:5001/sample-api:dev --run-id conc-4 --out results/conc-4
3 tests running (max 3); waiting for a slot...
3 tests running (max 3); waiting for a slot...      # repeated every 15 s, 7 times
...

# namespaces over time (sampled every 10 s)
13:02:01  lt-sample-api-conc-1:Active lt-sample-api-conc-2:Active lt-sample-api-conc-3:Active
13:03:32  lt-sample-api-conc-1:Terminating lt-sample-api-conc-2:Active lt-sample-api-conc-3:Active
13:03:42  lt-sample-api-conc-2:Terminating lt-sample-api-conc-3:Active lt-sample-api-conc-4:Active   # 4th admitted
```

conc-4 only created its namespace after conc-1 finished.

**Known limit of this single 4-CPU node:** each test requests 2 CPU at peak (service
250m + Postgres 500m + WireMock 250m + k6 1000m), while the node has 4 CPU
allocatable with 1.2 CPU already requested by system pods, which leaves 2.8 CPU. Three
concurrent tests (6 CPU) do not fit, so in this demo the k6 pods stayed `Pending` until
their Job deadline and all four runs ended `## ⚠️ ERROR` (exit 3, "no results collected").
On a right-sized cluster (or the cloud node pool) the cap and the capacity are aligned.
On this laptop, run one test at a time.

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
