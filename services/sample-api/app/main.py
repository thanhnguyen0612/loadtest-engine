"""Sample product-team service used to demonstrate the load-testing platform."""
import os
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

MAX_LIMIT = 100


class PaymentError(Exception):
    pass


class Repo:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def ping(self) -> None:
        with self.pool.connection() as conn:
            conn.execute("SELECT 1")

    def list_items(self, limit: int) -> list[dict]:
        with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute("SELECT id, name, price_cents FROM items ORDER BY id LIMIT %s", (limit,))
            return cur.fetchall()

    def get_item(self, item_id: int) -> dict | None:
        with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute("SELECT id, name, price_cents FROM items WHERE id = %s", (item_id,))
            return cur.fetchone()

    def insert_order(self, item_id: int, quantity: int) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO orders (item_id, quantity) VALUES (%s, %s) RETURNING id",
                (item_id, quantity),
            ).fetchone()
            return row[0]


class Payments:
    def __init__(self, client: httpx.Client, base_url: str):
        self.client = client
        self.base_url = base_url.rstrip("/")

    def charge(self, amount_cents: int) -> None:
        try:
            resp = self.client.post(f"{self.base_url}/charge", json={"amount_cents": amount_cents})
        except httpx.HTTPError as exc:
            raise PaymentError(str(exc)) from exc
        if not resp.is_success:
            raise PaymentError(f"payments returned {resp.status_code}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pool is opened here (not at import) so the module imports without a database.
    pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=2, max_size=10, open=False)
    pool.open(wait=True, timeout=30)
    client = httpx.Client(timeout=2.0)
    app.state.repo = Repo(pool)
    app.state.payments = Payments(client, os.environ["PAYMENTS_API_URL"])
    yield
    client.close()
    pool.close()


app = FastAPI(title="sample-api", lifespan=lifespan)


def get_repo() -> Repo:
    return app.state.repo


def get_payments() -> Payments:
    return app.state.payments


RepoDep = Annotated[Repo, Depends(get_repo)]
PaymentsDep = Annotated[Payments, Depends(get_payments)]


class OrderIn(BaseModel):
    item_id: int
    quantity: int = Field(gt=0)


@app.get("/healthz")
def healthz(repo: RepoDep):
    repo.ping()
    return {"status": "ok"}


@app.get("/items")
def items(repo: RepoDep, limit: Annotated[int, Query(ge=1)] = 20):
    return repo.list_items(min(limit, MAX_LIMIT))


@app.post("/orders", status_code=201)
def create_order(order: OrderIn, repo: RepoDep, payments: PaymentsDep):
    item = repo.get_item(order.item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    try:
        payments.charge(item["price_cents"] * order.quantity)
    except PaymentError as exc:
        raise HTTPException(502, f"payment failed: {exc}") from exc
    return {"order_id": repo.insert_order(order.item_id, order.quantity), "status": "approved"}
