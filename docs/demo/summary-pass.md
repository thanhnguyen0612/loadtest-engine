## ✅ PASS — sample-api

Target 50 rps for 60s in `lt-sample-api-pass-1` · est. cost <$0.01 (2.00 vCPU, 6 min)

### Metrics

| Metric | Value |
|---|---|
| p50 latency | 6 ms |
| p95 latency | 86 ms |
| p99 latency | 92 ms |
| Throughput | 40.1 req/s |
| Error rate | 0.00% |

### Thresholds

| Result | Check | Limit | Actual |
|---|---|---|---|
| ✅ | error rate | < 1% | 0.00% |
| ✅ | request rate (platform cap) | <= 500/s | 40.1/s |
| ✅ | p95 latency | < 300 ms | 86 ms |

### Resources

| Resource | Peak | Limit |
|---|---|---|
| Peak CPU | 98m | 250m |
| Peak memory | 114Mi | 256Mi |
