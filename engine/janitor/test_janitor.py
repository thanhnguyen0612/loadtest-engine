import io
import json
import urllib.error
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import janitor
from janitor import expired_namespaces, main
from loadtest import limits

NOW = 1_000_000


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ns(name, expires=None, managed=True, phase="Active", created=None):
    labels = {"loadtest.platform/managed": "true"} if managed else {}
    annotations = {} if expires is None else {"loadtest.platform/expires-at": str(expires)}
    meta = {"name": name, "labels": labels, "annotations": annotations}
    if created is not None:
        meta["creationTimestamp"] = iso(created)
    return {"metadata": meta, "status": {"phase": phase}}


def test_expired_namespaces_selects_only_past_expiry_and_managed():
    items = [
        ns("old", expires=900),
        ns("fresh", expires=2000),
        ns("unmanaged", expires=1, managed=False),
        ns("dying", expires=1, phase="Terminating"),
    ]
    assert expired_namespaces(items, now=1000) == ["old"]


def test_expiry_equal_to_now_is_not_expired():
    assert expired_namespaces([ns("edge", expires=1000)], now=1000) == []


def test_missing_annotation_uses_creation_age():
    items = [
        ns("stale", created=NOW - 2116),
        ns("young", created=NOW - 2114),
        ns("boundary", created=NOW - 2115),
    ]
    assert expired_namespaces(items, now=NOW) == ["stale"]


def test_unparseable_annotation_uses_creation_age():
    stale = ns("stale", expires="soon", created=NOW - 5000)
    young = ns("young", expires="soon", created=NOW - 10)
    assert expired_namespaces([stale, young], now=NOW) == ["stale"]


def test_missing_annotation_and_timestamp_is_kept():
    assert expired_namespaces([ns("mystery")], now=NOW) == []


def test_terminating_without_annotation_is_skipped():
    items = [ns("dying", phase="Terminating", created=NOW - 99999)]
    assert expired_namespaces(items, now=NOW) == []


def test_constants_match_cli_limits():
    assert janitor.MAX_DURATION_S == limits.MAX_DURATION_S
    assert janitor.TTL_BUFFER_S == limits.TTL_BUFFER_S
    assert janitor.SMOKE_DURATION_S == limits.SMOKE_DURATION_S
    assert janitor.PROVISION_TIMEOUT_S == limits.PROVISION_TIMEOUT_S
    # The creation-age fallback must never fire before a valid expires-at would.
    assert janitor.MAX_LIFETIME_S == limits.MAX_NAMESPACE_LIFETIME_S
    assert janitor.MANAGED_LABEL == limits.MANAGED_LABEL
    assert janitor.EXPIRES_ANNOTATION == limits.EXPIRES_ANNOTATION


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeApi:
    def __init__(self, items, fail=()):
        self.items = items
        self.fail = set(fail)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url, dict(request.header_items())))
        if request.get_method() == "GET":
            return FakeResponse(json.dumps({"items": self.items}).encode())
        name = request.full_url.rsplit("/", 1)[1]
        if name in self.fail:
            raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, None)
        return FakeResponse(b"{}")


def run(api, capsys, **kw):
    code = main(opener=api, now=NOW, token="tok", env={}, **kw)
    return code, capsys.readouterr().out


def test_main_deletes_only_expired_and_logs_each(capsys):
    api = FakeApi([ns("old", expires=NOW - 1), ns("fresh", expires=NOW + 100)])
    code, out = run(api, capsys)
    assert code == 0
    (get, delete) = api.requests
    parsed = urlparse(get[1])
    assert parsed.scheme + "://" + parsed.netloc == "https://kubernetes.default.svc"
    assert parsed.path == "/api/v1/namespaces"
    assert parse_qs(parsed.query) == {"labelSelector": ["loadtest.platform/managed=true"]}
    assert "labelSelector=loadtest.platform%2Fmanaged%3Dtrue" in get[1]
    assert get[2]["Authorization"] == "Bearer tok"
    assert delete[0] == "DELETE"
    assert delete[1] == "https://kubernetes.default.svc/api/v1/namespaces/old"
    assert out.splitlines() == ["deleted namespace old"]


def test_main_honors_service_env(capsys):
    api = FakeApi([ns("old", expires=1)])
    env = {"KUBERNETES_SERVICE_HOST": "10.0.0.1", "KUBERNETES_SERVICE_PORT": "443"}
    code = main(opener=api, now=NOW, token="t", env=env)
    assert code == 0
    assert api.requests[0][1].startswith("https://10.0.0.1:443/api/v1/namespaces?")


def test_main_failed_delete_logs_continues_and_exits_1(capsys):
    api = FakeApi([ns("bad", expires=1), ns("good", expires=1)], fail={"bad"})
    code, out = run(api, capsys)
    assert code == 1
    assert [r[0] for r in api.requests] == ["GET", "DELETE", "DELETE"]
    assert "failed to delete namespace bad" in out
    assert "deleted namespace good" in out


def test_main_nothing_expired_exits_0(capsys):
    code, out = run(FakeApi([ns("fresh", expires=NOW + 5)]), capsys)
    assert code == 0
    assert out == ""
