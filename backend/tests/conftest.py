import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/escrow_test")
os.environ["DEMO_MODE"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.db import tx
from app.main import app
from app.seed import seed
from app.security import login_limiter


class Api:
    def __init__(self, client: TestClient, login: str):
        self.c = client
        r = client.post("/api/auth/login", json={"login": login, "password": "demo2026"})
        assert r.status_code == 200, r.text
        self.h = {"Authorization": f"Bearer {r.json()['token']}"}

    def get(self, path):
        return self.c.get(path, headers=self.h)

    def post(self, path, body=None, key=None):
        return self.c.post(path, json=body or {}, headers={**self.h, "Idempotency-Key": key or str(uuid.uuid4())})

    def ok(self, method, path, body=None, key=None):
        r = getattr(self, method)(path, body, key) if method == "post" else self.get(path)
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean(client):
    with tx() as conn:
        seed(conn, reset=True)
    login_limiter._hits.clear()
    yield


@pytest.fixture
def alfa(client): return Api(client, "alfa")
@pytest.fixture
def beta(client): return Api(client, "beta")
@pytest.fixture
def carrier(client): return Api(client, "transline")
@pytest.fixture
def other_carrier(client): return Api(client, "severtrans")
@pytest.fixture
def operator(client): return Api(client, "operator")
