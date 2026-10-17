import pytest
from fastapi.testclient import TestClient

from app.main import PaymentError, app, get_payments, get_repo


class FakeRepo:
    def __init__(self):
        self.items = {i: {"id": i, "name": f"item-{i}", "price_cents": 100 * i} for i in range(1, 201)}
        self.orders = []

    def ping(self):
        return None

    def list_items(self, limit):
        return list(self.items.values())[:limit]

    def get_item(self, item_id):
        return self.items.get(item_id)

    def insert_order(self, item_id, quantity):
        self.orders.append((item_id, quantity))
        return len(self.orders)


class FakePayments:
    def __init__(self, fail=False):
        self.fail = fail
        self.charges = []

    def charge(self, amount_cents):
        if self.fail:
            raise PaymentError("declined")
        self.charges.append(amount_cents)


@pytest.fixture
def ctx():
    repo, pay = FakeRepo(), FakePayments()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_payments] = lambda: pay
    yield TestClient(app), repo, pay
    app.dependency_overrides.clear()


def test_healthz_ok(ctx):
    client, _, _ = ctx
    assert client.get("/healthz").json() == {"status": "ok"}


def test_items_default_and_shape(ctx):
    client, _, _ = ctx
    body = client.get("/items").json()
    assert len(body) == 20
    assert set(body[0]) == {"id", "name", "price_cents"}


def test_items_limit_capped(ctx):
    client, _, _ = ctx
    assert len(client.get("/items?limit=1000").json()) <= 100


def test_create_order_charges_and_returns_201(ctx):
    client, repo, pay = ctx
    r = client.post("/orders", json={"item_id": 2, "quantity": 3})
    assert r.status_code == 201
    assert r.json() == {"order_id": 1, "status": "approved"}
    assert pay.charges == [600]
    assert repo.orders == [(2, 3)]


def test_order_unknown_item_404(ctx):
    client, _, pay = ctx
    assert client.post("/orders", json={"item_id": 9999, "quantity": 1}).status_code == 404
    assert pay.charges == []


def test_order_payment_failure_502(ctx):
    client, repo, _ = ctx
    app.dependency_overrides[get_payments] = lambda: FakePayments(fail=True)
    assert client.post("/orders", json={"item_id": 1, "quantity": 1}).status_code == 502
    assert repo.orders == []
