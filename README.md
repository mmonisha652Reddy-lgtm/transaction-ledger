# Transaction Ledger API

A backend API (plus a small web UI) for a simple transaction ledger: create accounts, post credit and debit transactions, check balances, browse transaction history, and transfer money between accounts.

**Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2 · SQLite · Pydantic v2 · pytest

FastAPI was chosen because it gives request validation and interactive API documentation (Swagger UI) out of the box. SQLite was chosen so the project runs locally with no database server to install. It still enforces foreign keys, unique and check constraints, triggers, and atomic transactions.

---

## Running locally

```bash
cd transaction-ledger-api
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

| URL | What |
|---|---|
| http://localhost:8000/ | Web UI (accounts, transactions, history, transfers) |
| http://localhost:8000/docs | Interactive API documentation (Swagger UI) |
| http://localhost:8000/redoc | API documentation (ReDoc) |
| http://localhost:8000/openapi.json | OpenAPI 3 spec (also saved in [docs/openapi.json](docs/openapi.json)) |

The database file `ledger.db` is created automatically on first start. Delete it to reset all data. To use another database file, set `DATABASE_URL` (for example `DATABASE_URL=sqlite:///./other.db`).

### Live demo (Render)

The repo includes a [render.yaml](render.yaml), so it can be deployed to [Render](https://render.com) as a Blueprint: **New → Blueprint →** select this repo. Render installs the requirements and starts the app with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

On Render's free plan the filesystem is temporary, so the SQLite data resets whenever the service restarts or wakes from sleep. The first request after a period of inactivity can take about 30–60 seconds while the service wakes up.

### Running the tests

```bash
pytest -q
```

The 45 tests cover every requirement in the brief, including concurrent debits, concurrent duplicate submissions, and rollback on failure.

---

## Project structure

```
app/
  main.py              App setup, routers, error handlers, serves the UI
  config.py            Settings (DATABASE_URL, page sizes)
  database.py          Engine, session, transaction locking
  models.py            Tables, constraints, immutability triggers
  schemas.py           Request/response models and validation rules
  errors.py            Error types and the shared error response format
  money.py             Converts between amounts and stored integer cents
  services/ledger.py   Business logic: balances, duplicates, atomic writes, transfers
  routers/accounts.py  Account, balance, transaction and history endpoints
  routers/transfers.py Transfer endpoint
  static/index.html    Frontend (plain HTML + JavaScript, no build step)
tests/                 pytest suite
docs/openapi.json      Exported API specification
```

---

## API documentation

Base URL: `http://localhost:8000`. All request and response bodies are JSON.

### Summary

| Method | Path | Description | Success |
|---|---|---|---|
| POST | `/accounts` | Create an account | 201 |
| GET | `/accounts` | List accounts (paginated) | 200 |
| GET | `/accounts/{account_id}` | Get an account | 200 |
| GET | `/accounts/{account_id}/balance` | Get the account balance | 200 |
| POST | `/accounts/{account_id}/transactions` | Add a credit or debit | 201 |
| GET | `/accounts/{account_id}/transactions` | Transaction history (filter, paginate, latest first) | 200 |
| POST | `/transfers` | Transfer between accounts (bonus) | 201 |
| GET | `/health` | Health check | 200 |

There are no update or delete endpoints for transactions. Sending `PUT`, `PATCH` or `DELETE` returns `405`.

### 1. Create account: `POST /accounts`

Request:
```json
{ "name": "Alice" }
```
The only allowed field is `name`. If the request also includes `balance`, `id` or `created_at`, it is rejected with `400`.

Response `201`:
```json
{
  "id": "68906772-85f3-46b8-ab7f-b33d9dd5b5bb",
  "name": "Alice",
  "balance": 0.0,
  "created_at": "2026-10-28T12:16:58.401221Z"
}
```

### 2. Add transaction: `POST /accounts/{account_id}/transactions`

Request:
```json
{
  "type": "credit",
  "amount": 100.50,
  "description": "Salary",
  "reference_id": "INV-1001"
}
```

| Field | Rules |
|---|---|
| `type` | Required. `credit` or `debit` |
| `amount` | Required. Greater than 0, at most 2 decimal places, at most 1,000,000,000. Can be sent as a number or a string |
| `reference_id` | Required. 1–100 characters. Must be unique within the account |
| `description` | Optional. Up to 255 characters |

Response `201`:
```json
{
  "id": "b67586ef-6a44-40a8-b8ea-a7593605f96e",
  "account_id": "68906772-85f3-46b8-ab7f-b33d9dd5b5bb",
  "type": "credit",
  "amount": 100.5,
  "description": "Salary",
  "reference_id": "INV-1001",
  "balance_after": 100.5,
  "transfer_id": null,
  "created_at": "2026-10-28T12:16:58.421264Z"
}
```

Rules:
- A **credit** adds `amount` to the balance. A **debit** subtracts it.
- A debit larger than the current balance is rejected with `422 INSUFFICIENT_BALANCE`. Nothing is saved and the balance does not change.
- Sending a `reference_id` that this account has already used returns `409 DUPLICATE_TRANSACTION`. No new transaction is created, the balance does not change, and the existing transaction is returned in `error.details.existing_transaction`. Other accounts can use the same `reference_id`.

### 3. Get balance: `GET /accounts/{account_id}/balance`

Response `200`:
```json
{ "account_id": "68906772-...", "name": "Alice", "balance": 70.0 }
```
If the account does not exist, the response is `404 ACCOUNT_NOT_FOUND`.

### 4. Transaction history: `GET /accounts/{account_id}/transactions`

| Query param | Default | Rules |
|---|---|---|
| `type` | (both) | `credit` or `debit`. If omitted, both types are returned |
| `page` | 1 | Integer, 1 or more |
| `limit` | 20 | Integer, 1–100 |

Results are sorted latest first by `created_at`.

Response `200`:
```json
{
  "data": [ { "id": "...", "type": "debit", "amount": 30.0, "...": "..." } ],
  "pagination": { "page": 1, "limit": 20, "total_records": 2, "total_pages": 1 }
}
```
A page number beyond the last page returns an empty `data` list, together with the correct totals.

### 5. Transfer (bonus): `POST /transfers`

Request:
```json
{
  "from_account_id": "68906772-...",
  "to_account_id": "3d47b1f6-...",
  "amount": 30,
  "reference_id": "TRF-1001",
  "description": "Rent share"
}
```

Response `201`:
```json
{
  "transfer_id": "56ad2ca6-...",
  "from_account_id": "68906772-...",
  "to_account_id": "3d47b1f6-...",
  "amount": 30.0,
  "debit_transaction": { "type": "debit", "transfer_id": "56ad2ca6-...", "...": "..." },
  "credit_transaction": { "type": "credit", "transfer_id": "56ad2ca6-...", "...": "..." }
}
```
A transfer creates a debit on the source account and a credit on the destination account. Both are saved in one database transaction and share the same `transfer_id`. If any check fails (account missing, insufficient balance, duplicate reference), nothing is saved.

### Error format

Every error response has the same shape:
```json
{
  "error": {
    "code": "INSUFFICIENT_BALANCE",
    "message": "Insufficient balance for this debit.",
    "details": { "account_id": "...", "balance": 100.0, "requested_amount": 150.0 }
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Missing or invalid fields (e.g. empty name, missing `reference_id`, unknown fields) |
| 400 | `INVALID_REQUEST_BODY` | Body is not valid JSON |
| 400 | `INVALID_TRANSACTION_TYPE` | `type` is not `credit` or `debit` (in the body or the history filter) |
| 400 | `INVALID_AMOUNT` | Amount is ≤ 0, not a number, has more than 2 decimals, or is too large |
| 400 | `INVALID_PAGINATION` | `page` < 1, or `limit` outside 1–100, or either is not an integer |
| 400 | `SAME_ACCOUNT_TRANSFER` | Transfer source and destination are the same account |
| 404 | `ACCOUNT_NOT_FOUND` | The account ID does not exist |
| 404 | `NOT_FOUND` | Unknown URL |
| 405 | `METHOD_NOT_ALLOWED` | e.g. trying to edit or delete transactions |
| 409 | `DUPLICATE_TRANSACTION` | `reference_id` already used on this account |
| 422 | `INSUFFICIENT_BALANCE` | Debit or transfer amount is more than the balance |
| 500 | `INTERNAL_ERROR` | Unexpected server error (no internal details are exposed) |

For validation errors, `details` lists each problem as `{ "field": "...", "message": "..." }`.

### Example session (curl)

```bash
A=$(curl -s -X POST localhost:8000/accounts -H 'Content-Type: application/json' -d '{"name":"Alice"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -X POST localhost:8000/accounts/$A/transactions -H 'Content-Type: application/json' \
     -d '{"type":"credit","amount":100,"reference_id":"R1","description":"Salary"}'
curl localhost:8000/accounts/$A/balance
curl "localhost:8000/accounts/$A/transactions?type=credit&page=1&limit=10"
```

---

## How data integrity is guaranteed

| Requirement | How |
|---|---|
| **Atomicity** | Each write runs inside one database transaction (`with db.begin()`). The balance update and the transaction insert are committed together. If either fails, both are rolled back. A transfer's debit and credit share a single transaction. |
| **No race conditions** | Transactions start with `BEGIN IMMEDIATE`, so concurrent writes are handled one at a time. Two parallel debits cannot both read the same balance. Tests fire 10 parallel debits and 8 parallel duplicate submissions to check this. |
| **Balance consistency** | The balance changes only inside the same transaction that records the ledger entry. Each transaction also stores `balance_after` for auditing. A `CHECK (balance >= 0)` constraint means the database itself refuses an overdraft. |
| **Duplicate protection** | The application checks for an existing reference first, and a `UNIQUE (account_id, reference_id)` constraint backs this up even under concurrent requests. |
| **Immutability** | The API has no update or delete endpoints, and database triggers reject any `UPDATE` or `DELETE` on the `transactions` table. |
| **Other constraints** | Foreign key from transaction to account, `CHECK (amount > 0)`, `CHECK (type IN ('credit','debit'))`. |
| **Money precision** | Amounts are stored as integer cents, so there are no floating point rounding errors (ten credits of 0.10 sum to exactly 1.00). |

---

## Assumptions

1. **Single currency.** All accounts use the same currency, and amounts have at most 2 decimal places. Internally they are stored as integer cents and returned as JSON numbers (e.g. `100.5`).
2. **IDs are generated by the server** as UUIDs, for both accounts and transactions. Clients cannot supply them.
3. **No authentication.** The brief does not mention users or auth, so every endpoint is open.
4. **A rejected transaction is not stored.** An insufficient-balance debit is not recorded, so its `reference_id` stays free and can be used again later.
5. **A duplicate is detected by `reference_id` only.** Re-sending the same `reference_id` returns `409` with the original transaction, even if the amount or type differs. This lets a client safely retry a request.
6. **Status codes:** insufficient balance → `422` (the request is valid but cannot be processed), duplicate → `409` (conflict), invalid input → `400`.
7. **`description` is optional** and defaults to an empty string.
8. **Pagination:** `limit` defaults to 20 and is capped at 100. A page number past the end returns an empty list, not an error.
9. **Ordering:** "latest first" uses `created_at` (UTC, microsecond precision). If two rows have the same timestamp, insertion order breaks the tie.
10. **Transfers** create two linked ledger entries (a debit on the source, a credit on the destination) with a shared `transfer_id`. The transfer's `reference_id` must not already be used on either account. Transferring to the same account is rejected. If no description is given, it defaults to "Transfer".
11. **Accounts cannot be deleted or renamed.** The brief does not ask for this, and keeping accounts preserves the ledger history.
12. **Extra endpoints** `GET /accounts` and `GET /accounts/{id}` were added so the UI can list and select accounts.
13. **SQLite** is used for easy local setup. To use PostgreSQL, change `DATABASE_URL` and replace the SQLite-specific `BEGIN IMMEDIATE` and triggers with `SELECT ... FOR UPDATE` and a Postgres trigger. The service layer would not need to change.
