## ❌ FAIL — sample-api

Target 50 rps for 60s in `lt-sample-api-fail-1` · est. cost ~$0.00 (2.00 vCPU, 6 min)

### Metrics

| Metric | Value |
|---|---|
| p50 latency | 8 ms |
| p95 latency | 87 ms |
| p99 latency | 92 ms |
| Throughput | 40.1 req/s |
| Error rate | 0.00% |

### Thresholds

| Result | Check | Limit | Actual |
|---|---|---|---|
| ❌ | p95 latency | < 1 ms | 87 ms |
| ✅ | error rate | < 1% | 0.00% |

### Resources

| Resource | Peak | Limit |
|---|---|---|
| Peak CPU | 143m | 250m |
| Peak memory | 114Mi | 256Mi |
