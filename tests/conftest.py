import os
import tempfile

# Use a throwaway database file for tests (must be set before the app is imported).
_db_dir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_db_dir}/test.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def make_account(client):
    def _make(name="Alice"):
        r = client.post("/accounts", json={"name": name})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    return _make


@pytest.fixture()
def tx(client):
    def _tx(account_id, type_, amount, ref, description="test"):
        return client.post(
            f"/accounts/{account_id}/transactions",
            json={"type": type_, "amount": amount, "reference_id": ref, "description": description},
        )
    return _tx
