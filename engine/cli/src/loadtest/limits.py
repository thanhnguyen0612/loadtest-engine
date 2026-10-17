"""Platform caps, presets and approved images. Mirrored in Terraform; keep in sync."""

MAX_RPS = 500
MAX_DURATION_S = 900
MAX_CPU_M = 2000
MAX_MEMORY_MI = 2048
MAX_CONCURRENT_TESTS = 3
TTL_BUFFER_S = 900
MAX_LATENCY_MS = 5000

ALLOWED_REGISTRIES: tuple[str, ...] = ("localhost:5001",)

PLATFORM_IMAGES: dict[str, str] = {
    "postgres": "docker.io/library/postgres:16-alpine",
    "wiremock": "docker.io/wiremock/wiremock:3.9.1",
    "k6": "docker.io/grafana/k6:0.54.0",
}

# size -> (target rps, duration seconds)
PRESETS: dict[str, tuple[int, int]] = {
    "S": (50, 60),
    "M": (150, 300),
    "L": (500, 900),
}

# Platform component sizes (cpu millicores, memory Mi); used for pod limits and the quota.
POSTGRES_CPU_M, POSTGRES_MEMORY_MI = 500, 512
STUB_CPU_M, STUB_MEMORY_MI = 250, 256
K6_CPU_M, K6_MEMORY_MI = 1000, 1024

# Label/annotation keys stamped on every test namespace (mirrored in engine/janitor/janitor.py).
MANAGED_LABEL = "loadtest.platform/managed"
TEAM_LABEL = "loadtest.platform/team"
SERVICE_LABEL = "loadtest.platform/service"
RUN_ID_LABEL = "loadtest.platform/run-id"
EXPIRES_ANNOTATION = "loadtest.platform/expires-at"  # unix epoch seconds
