import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.database import engine


def balance(client, account_id):
    r = client.get(f"/accounts/{account_id}/balance")
    assert r.status_code == 200
    return r.json()["balance"]


# ---------- 1. accounts ----------

def test_create_account(client):
    r = client.post("/accounts", json={"name": "  Alice  "})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Alice"
    assert body["balance"] == 0
    assert body["id"] and body["created_at"].endswith("Z")


def test_account_ids_unique(client, make_account):
    assert len({make_account() for _ in range(5)}) == 5


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "   "}, {"name": 123}])
def test_account_name_required(client, payload):
    r = client.post("/accounts", json=payload)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_client_cannot_set_balance(client):
    r = client.post("/accounts", json={"name": "Bob", "balance": 500})
    assert r.status_code == 400


def test_client_cannot_set_id_or_created_at(client):
    assert client.post("/accounts", json={"name": "Bob", "id": "x"}).status_code == 400
    assert client.post("/accounts", json={"name": "Bob", "created_at": "2020-01-01"}).status_code == 400


def test_invalid_json_body(client):
    r = client.post("/accounts", content="{bad json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST_BODY"


# ---------- 2. transactions ----------

def test_credit_increases_balance(client, make_account, tx):
    a = make_account()
    tx(a, "credit", 100, "r1")
    r = tx(a, "credit", 50, "r2")
    assert r.status_code == 201
    t = r.json()
    assert t["type"] == "credit" and t["amount"] == 50 and t["balance_after"] == 150
    assert t["account_id"] == a and t["reference_id"] == "r2" and t["id"] and t["created_at"]
    assert balance(client, a) == 150


def test_debit_decreases_balance(client, make_account, tx):
    a = make_account()
    tx(a, "credit", 100, "r1")
    assert tx(a, "debit", 30, "r2").status_code == 201
    assert balance(client, a) == 70


def test_debit_insufficient_balance_rejected(client, make_account, tx):
    a = make_account()
    tx(a, "credit", 100, "r1")
    r = tx(a, "debit", 150, "r2")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
    assert balance(client, a) == 100
    # rejected debit is not recorded, so the reference can be reused
    assert client.get(f"/accounts/{a}/transactions").json()["pagination"]["total_records"] == 1
    assert tx(a, "debit", 100, "r2").status_code == 201
    assert balance(client, a) == 0


def test_duplicate_reference_same_account(client, make_account, tx):
    a = make_account()
    first = tx(a, "credit", 100, "dup").json()
    r = tx(a, "credit", 100, "dup")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "DUPLICATE_TRANSACTION"
    assert err["details"]["existing_transaction"]["id"] == first["id"]
    assert balance(client, a) == 100
    assert client.get(f"/accounts/{a}/transactions").json()["pagination"]["total_records"] == 1


def test_same_reference_different_accounts(client, make_account, tx):
    a, b = make_account("A"), make_account("B")
    assert tx(a, "credit", 10, "same").status_code == 201
    assert tx(b, "credit", 10, "same").status_code == 201


def test_transaction_unknown_account(client, tx):
    r = tx("does-not-exist", "credit", 10, "r1")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"type": "refund", "amount": 10, "reference_id": "r"}, "INVALID_TRANSACTION_TYPE"),
        ({"type": "credit", "amount": 0, "reference_id": "r"}, "INVALID_AMOUNT"),
        ({"type": "credit", "amount": -5, "reference_id": "r"}, "INVALID_AMOUNT"),
        ({"type": "credit", "amount": 1.234, "reference_id": "r"}, "INVALID_AMOUNT"),
        ({"type": "credit", "amount": "abc", "reference_id": "r"}, "INVALID_AMOUNT"),
        ({"type": "credit", "amount": 10}, "VALIDATION_ERROR"),
        ({"type": "credit", "amount": 10, "reference_id": "  "}, "VALIDATION_ERROR"),
    ],
)
def test_transaction_validation(client, make_account, payload, code):
    a = make_account()
    r = client.post(f"/accounts/{a}/transactions", json=payload)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == code
    assert balance(client, a) == 0


def test_decimal_amounts(client, make_account, tx):
    a = make_account()
    for i in range(10):
        tx(a, "credit", 0.1, f"r{i}")
    assert balance(client, a) == 1.0  # no float drift


# ---------- 3. balance ----------

def test_balance_unknown_account(client):
    r = client.get("/accounts/nope/balance")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_balance_fields(client, make_account):
    a = make_account()
    assert client.get(f"/accounts/{a}/balance").json() == {"account_id": a, "name": "Alice", "balance": 0}


# ---------- 4. history ----------

def test_history_filter_pagination_sorting(client, make_account, tx):
    a = make_account()
    for i in range(1, 8):
        tx(a, "credit", 100, f"c{i}")
    for i in range(1, 4):
        tx(a, "debit", 10, f"d{i}")

    r = client.get(f"/accounts/{a}/transactions", params={"page": 1, "limit": 4}).json()
    assert r["pagination"] == {"page": 1, "limit": 4, "total_records": 10, "total_pages": 3}
    assert [t["reference_id"] for t in r["data"]] == ["d3", "d2", "d1", "c7"]  # latest first

    last = client.get(f"/accounts/{a}/transactions", params={"page": 3, "limit": 4}).json()
    assert [t["reference_id"] for t in last["data"]] == ["c2", "c1"]

    debits = client.get(f"/accounts/{a}/transactions", params={"type": "debit"}).json()
    assert debits["pagination"]["total_records"] == 3
    assert all(t["type"] == "debit" for t in debits["data"])

    credits = client.get(f"/accounts/{a}/transactions", params={"type": "credit"}).json()
    assert credits["pagination"]["total_records"] == 7

    beyond = client.get(f"/accounts/{a}/transactions", params={"page": 99}).json()
    assert beyond["data"] == [] and beyond["pagination"]["total_records"] == 10


@pytest.mark.parametrize(
    "params,code",
    [
        ({"page": 0}, "INVALID_PAGINATION"),
        ({"page": "x"}, "INVALID_PAGINATION"),
        ({"limit": 0}, "INVALID_PAGINATION"),
        ({"limit": 1000}, "INVALID_PAGINATION"),
        ({"type": "refund"}, "INVALID_TRANSACTION_TYPE"),
    ],
)
def test_history_invalid_params(client, make_account, params, code):
    a = make_account()
    r = client.get(f"/accounts/{a}/transactions", params=params)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == code


def test_history_unknown_account(client):
    assert client.get("/accounts/nope/transactions").status_code == 404


# ---------- 5. integrity ----------

def test_balance_consistency_example(client, make_account, tx):
    a = make_account()
    tx(a, "credit", 100, "1")
    tx(a, "credit", 50, "2")
    tx(a, "debit", 30, "3")
    tx(a, "debit", 20, "4")
    tx(a, "debit", 500, "5")  # rejected
    assert balance(client, a) == 100


def test_transactions_not_editable_via_api(client, make_account, tx):
    a = make_account()
    t = tx(a, "credit", 10, "r1").json()
    for method in ("put", "patch", "delete"):
        r = getattr(client, method)(f"/accounts/{a}/transactions")
        assert r.status_code == 405
        assert r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert client.get(f"/accounts/{a}/transactions").json()["data"][0] == t


def test_transactions_immutable_in_database(client, make_account, tx):
    a = make_account()
    tx(a, "credit", 10, "r1")
    with engine.connect() as conn:
        with pytest.raises(DatabaseError):
            conn.execute(text("UPDATE transactions SET amount = 999"))
        conn.rollback()
        with pytest.raises(DatabaseError):
            conn.execute(text("DELETE FROM transactions"))
        conn.rollback()


def test_database_rejects_negative_balance(client, make_account):
    a = make_account()
    with engine.connect() as conn:
        with pytest.raises(DatabaseError):
            conn.execute(text("UPDATE accounts SET balance = -1 WHERE id = :id"), {"id": a})
        conn.rollback()


def test_atomicity_rollback_on_failure(client, make_account, monkeypatch):
    """If saving the transaction fails, the balance change is rolled back too."""
    from app.services import ledger
    a = make_account()

    original = ledger.Transaction

    def broken(**kwargs):
        kwargs["type"] = "invalid"  # violates the CHECK constraint at flush time
        return original(**kwargs)

    monkeypatch.setattr(ledger, "Transaction", broken)
    with pytest.raises(Exception):
        client.post(f"/accounts/{a}/transactions",
                    json={"type": "credit", "amount": 50, "reference_id": "x"})
    monkeypatch.undo()
    assert balance(client, a) == 0


def test_concurrent_debits_never_overdraw(client, make_account, tx):
    a = make_account()
    tx(a, "credit", 100, "seed")
    results = []

    def worker(i):
        results.append(tx(a, "debit", 30, f"d{i}").status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(201) == 3
    assert results.count(422) == 7
    assert balance(client, a) == 10


def test_concurrent_duplicates_create_one(client, make_account, tx):
    a = make_account()
    results = []

    def worker():
        results.append(tx(a, "credit", 10, "same-ref").status_code)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [201] + [409] * 7
    assert balance(client, a) == 10


# ---------- 9. transfers ----------

def _transfer(client, src, dst, amount, ref="t1"):
    return client.post("/transfers", json={
        "from_account_id": src, "to_account_id": dst, "amount": amount, "reference_id": ref,
    })


def test_transfer_success(client, make_account, tx):
    a, b = make_account("A"), make_account("B")
    tx(a, "credit", 100, "s1")
    tx(b, "credit", 50, "s2")
    r = _transfer(client, a, b, 30)
    assert r.status_code == 201
    body = r.json()
    assert body["debit_transaction"]["transfer_id"] == body["credit_transaction"]["transfer_id"] == body["transfer_id"]
    assert balance(client, a) == 70
    assert balance(client, b) == 80


def test_transfer_insufficient_balance(client, make_account, tx):
    a, b = make_account("A"), make_account("B")
    tx(a, "credit", 10, "s1")
    r = _transfer(client, a, b, 30)
    assert r.status_code == 422
    assert balance(client, a) == 10 and balance(client, b) == 0


def test_transfer_missing_account_rolls_back(client, make_account, tx):
    a = make_account("A")
    tx(a, "credit", 100, "s1")
    r = _transfer(client, a, "missing", 30)
    assert r.status_code == 404
    assert balance(client, a) == 100
    assert client.get(f"/accounts/{a}/transactions").json()["pagination"]["total_records"] == 1


def test_transfer_partial_failure_rolls_back(client, make_account, tx):
    """Destination already used the reference: the source debit must be undone."""
    a, b = make_account("A"), make_account("B")
    tx(a, "credit", 100, "s1")
    tx(b, "credit", 5, "t1")
    r = _transfer(client, a, b, 30, ref="t1")
    assert r.status_code == 409
    assert balance(client, a) == 100 and balance(client, b) == 5


def test_transfer_same_account(client, make_account):
    a = make_account()
    r = _transfer(client, a, a, 10)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "SAME_ACCOUNT_TRANSFER"


def test_transfer_duplicate(client, make_account, tx):
    a, b = make_account("A"), make_account("B")
    tx(a, "credit", 100, "s1")
    assert _transfer(client, a, b, 30).status_code == 201
    assert _transfer(client, a, b, 30).status_code == 409
    assert balance(client, a) == 70 and balance(client, b) == 30


# ---------- UI support endpoints ----------

def test_account_summary(client, make_account, tx):
    a = make_account()
    s = client.get(f"/accounts/{a}/summary").json()
    assert s["transaction_count"] == 0 and s["last_transaction_at"] is None
    tx(a, "credit", 100, "1")
    tx(a, "credit", 50.5, "2")
    tx(a, "debit", 20, "3")
    s = client.get(f"/accounts/{a}/summary").json()
    assert s["total_credits"] == 150.5 and s["total_debits"] == 20
    assert s["credit_count"] == 2 and s["debit_count"] == 1 and s["transaction_count"] == 3
    assert s["balance"] == 130.5 and s["last_transaction_at"].endswith("Z")
    assert client.get("/accounts/nope/summary").status_code == 404


def test_stats(client, make_account, tx):
    assert client.get("/stats").json() == {"total_accounts": 0, "total_balance": 0, "total_transactions": 0}
    a, b = make_account("A"), make_account("B")
    tx(a, "credit", 100, "1")
    tx(b, "credit", 25, "1")
    assert client.get("/stats").json() == {"total_accounts": 2, "total_balance": 125, "total_transactions": 2}


# ---------- standardized error messages ----------

@pytest.mark.parametrize(
    "payload,field,message",
    [
        ({}, "name", "Account holder name is required."),
        ({"name": ""}, "name", "Account holder name is required."),
        ({"name": "   "}, "name", "Account holder name is required."),
        ({"name": None}, "name", "Account holder name is required."),
        ({"name": 5}, "name", "Account holder name must be text."),
        ({"name": "x" * 101}, "name", "Account holder name must be at most 100 characters."),
        ({"name": "A", "balance": 5}, "balance", "Balance cannot be set. New accounts always start with a balance of 0."),
    ],
)
def test_account_messages(client, payload, field, message):
    err = client.post("/accounts", json=payload).json()["error"]
    assert err["message"] == message
    assert err["details"] == [{"field": field, "message": message}]


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"type": "refund", "amount": 10, "reference_id": "r"}, "Transaction type must be either 'credit' or 'debit'."),
        ({"type": "credit", "amount": 0, "reference_id": "r"}, "Amount must be greater than 0."),
        ({"type": "credit", "amount": "abc", "reference_id": "r"}, "Amount must be a valid number."),
        ({"type": "credit", "amount": 1.234, "reference_id": "r"}, "Amount can have at most 2 decimal places."),
        ({"type": "credit", "amount": 1e10, "reference_id": "r"}, "Amount must not be more than 1,000,000,000."),
        ({"type": "credit", "amount": 10}, "Reference ID is required."),
        ({"type": "credit", "amount": 10, "reference_id": "r", "description": "d" * 300},
         "Description must be at most 255 characters."),
    ],
)
def test_transaction_messages(client, make_account, payload, message):
    a = make_account()
    assert client.post(f"/accounts/{a}/transactions", json=payload).json()["error"]["message"] == message


def test_multiple_errors_listed_per_field(client, make_account):
    a = make_account()
    err = client.post(f"/accounts/{a}/transactions", json={}).json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["message"] == "Some fields are invalid. Please correct them and try again."
    assert err["details"] == [
        {"field": "type", "message": "Transaction type is required."},
        {"field": "amount", "message": "Amount is required."},
        {"field": "reference_id", "message": "Reference ID is required."},
    ]


def test_business_and_query_messages(client, make_account, tx):
    a = make_account()
    tx(a, "credit", 5, "dup")
    assert tx(a, "debit", 10, "x").json()["error"]["message"] == \
        "Insufficient balance. Available balance is 5.00, but 10.00 was requested."
    assert tx(a, "credit", 5, "dup").json()["error"]["message"] == \
        "Duplicate transaction. Reference ID 'dup' has already been used for this account."
    assert client.get("/accounts/nope/balance").json()["error"]["message"] == \
        "Account not found. Please check the account ID."
    assert client.get(f"/accounts/{a}/transactions?page=0").json()["error"]["message"] == "Page must be 1 or more."
    assert client.get(f"/accounts/{a}/transactions?limit=500").json()["error"]["message"] == \
        "Limit must not be more than 100."
    assert client.get(f"/accounts/{a}/transactions?page=x").json()["error"]["message"] == "Page must be a whole number."


# ---------- edge cases ----------

def test_huge_page_number_is_rejected_not_crash(client, make_account):
    a = make_account()
    for url in (f"/accounts/{a}/transactions", "/accounts"):
        r = client.get(url, params={"page": 10**21})
        assert r.status_code == 400
        assert r.json()["error"]["message"] == "Page must not be more than 1,000,000,000."


def test_null_description_treated_as_empty(client, make_account):
    a = make_account()
    r = client.post(f"/accounts/{a}/transactions",
                    json={"type": "credit", "amount": 5, "reference_id": "n", "description": None})
    assert r.status_code == 201 and r.json()["description"] == ""


def test_boolean_amount_rejected(client, make_account):
    a = make_account()
    r = client.post(f"/accounts/{a}/transactions", json={"type": "credit", "amount": True, "reference_id": "b"})
    assert r.status_code == 400 and r.json()["error"]["message"] == "Amount must be a valid number."


def test_accounts_list_paginates_past_100(client, make_account):
    for i in range(105):
        make_account(f"User {i}")
    p2 = client.get("/accounts", params={"page": 2, "limit": 100}).json()
    assert p2["pagination"] == {"page": 2, "limit": 100, "total_records": 105, "total_pages": 2}
    assert len(p2["data"]) == 5


# ---------- delete account ----------

def _db_count(sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def test_delete_account_removes_account_and_transactions(client, make_account, tx):
    a, keep = make_account("Gone"), make_account("Stays")
    tx(a, "credit", 100, "1")
    tx(a, "debit", 40, "2")
    tx(keep, "credit", 10, "1")

    r = client.delete(f"/accounts/{a}")
    assert r.status_code == 200
    assert r.json() == {"deleted_account_id": a, "name": "Gone", "final_balance": 60, "deleted_transactions": 2}

    # really gone from SQLite
    assert _db_count("SELECT COUNT(*) FROM accounts WHERE id = :id", id=a) == 0
    assert _db_count("SELECT COUNT(*) FROM transactions WHERE account_id = :id", id=a) == 0
    assert _db_count("SELECT COUNT(*) FROM account_deletions") == 0
    # other accounts untouched
    assert balance(client, keep) == 10
    assert _db_count("SELECT COUNT(*) FROM transactions WHERE account_id = :id", id=keep) == 1
    # API now reports it as missing
    for url in (f"/accounts/{a}", f"/accounts/{a}/balance", f"/accounts/{a}/transactions"):
        assert client.get(url).status_code == 404
    assert client.get("/stats").json() == {"total_accounts": 1, "total_balance": 10, "total_transactions": 1}


def test_delete_unknown_account(client):
    r = client.delete("/accounts/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_delete_twice(client, make_account):
    a = make_account()
    assert client.delete(f"/accounts/{a}").status_code == 200
    assert client.delete(f"/accounts/{a}").status_code == 404


def test_delete_account_with_transfer_keeps_other_side(client, make_account, tx):
    a, b = make_account("A"), make_account("B")
    tx(a, "credit", 100, "s")
    client.post("/transfers", json={"from_account_id": a, "to_account_id": b, "amount": 30, "reference_id": "t"})
    assert client.delete(f"/accounts/{a}").status_code == 200
    assert balance(client, b) == 30
    hist = client.get(f"/accounts/{b}/transactions").json()["data"]
    assert len(hist) == 1 and hist[0]["transfer_id"]


def test_single_transaction_delete_still_blocked(client, make_account, tx):
    a = make_account()
    tx(a, "credit", 10, "r1")
    with engine.connect() as conn:
        with pytest.raises(DatabaseError):
            conn.execute(text("DELETE FROM transactions WHERE account_id = :id"), {"id": a})
        conn.rollback()
    assert balance(client, a) == 10


def test_delete_is_atomic(client, make_account, tx, monkeypatch):
    """If the account row can't be deleted, its transactions must come back too."""
    from app.services import ledger
    a = make_account()
    tx(a, "credit", 10, "r1")
    original_delete = ledger.delete

    def broken_delete(model):
        if model is ledger.Account:
            raise RuntimeError("simulated failure")
        return original_delete(model)

    monkeypatch.setattr(ledger, "delete", broken_delete)
    with pytest.raises(RuntimeError):
        client.delete(f"/accounts/{a}")
    monkeypatch.undo()
    assert balance(client, a) == 10
    assert _db_count("SELECT COUNT(*) FROM transactions WHERE account_id = :id", id=a) == 1
    assert _db_count("SELECT COUNT(*) FROM account_deletions") == 0


def test_reference_reusable_after_account_deleted(client, make_account, tx):
    a = make_account()
    tx(a, "credit", 10, "REF")
    client.delete(f"/accounts/{a}")
    b = make_account()
    assert tx(b, "credit", 10, "REF").status_code == 201
