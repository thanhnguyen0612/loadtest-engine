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
