## ❌ FAIL — sample-api

Target 50 rps for 60s in `lt-sample-api-fail-1` · est. cost <$0.01 (2.00 vCPU, 6 min)

### Metrics

| Metric | Value |
|---|---|
| p50 latency | 6 ms |
| p95 latency | 86 ms |
| p99 latency | 91 ms |
| Throughput | 40.1 req/s |
| Error rate | 0.00% |

### Thresholds

| Result | Check | Limit | Actual |
|---|---|---|---|
| ✅ | request rate (platform cap) | <= 500/s | 40.1/s |
| ❌ | p95 latency | < 1 ms | 86 ms |
| ✅ | error rate | < 1% | 0.00% |

### Resources

| Resource | Peak | Limit |
|---|---|---|
| Peak CPU | 173m | 250m |
| Peak memory | 114Mi | 256Mi |
