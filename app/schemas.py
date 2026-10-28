from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models import Account, Transaction
from app.money import from_cents

MAX_AMOUNT = Decimal("1000000000")  # 1 billion per transaction


class TransactionType(str, Enum):
    credit = "credit"
    debit = "debit"


# ---------- requests ----------

class RequestModel(BaseModel):
    # Unknown fields (for example "balance" on account creation) are rejected.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _OptionalDescription(RequestModel):
    @field_validator("description", mode="before", check_fields=False)
    @classmethod
    def _null_means_empty(cls, v):
        # Description is optional: null is treated the same as leaving it out.
        return "" if v is None else v


class AccountCreate(RequestModel):
    name: str = Field(min_length=1, max_length=100, examples=["Alice"])


class TransactionCreate(_OptionalDescription):
    type: TransactionType
    amount: Decimal = Field(gt=0, le=MAX_AMOUNT, decimal_places=2, examples=[100.50])
    description: str = Field(default="", max_length=255, examples=["Salary"])
    reference_id: str = Field(min_length=1, max_length=100, examples=["INV-1001"])


class TransferCreate(_OptionalDescription):
    from_account_id: str = Field(min_length=1)
    to_account_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=0, le=MAX_AMOUNT, decimal_places=2, examples=[30])
    description: str = Field(default="", max_length=255, examples=["Rent share"])
    reference_id: str = Field(min_length=1, max_length=100, examples=["TRF-1001"])


# ---------- responses ----------

def _utc(value: datetime) -> str:
    # SQLite drops timezone info; all timestamps are stored in UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


class AccountOut(BaseModel):
    id: str
    name: str
    balance: float
    created_at: datetime

    _ser_created = field_serializer("created_at")(lambda self, v: _utc(v))

    @classmethod
    def build(cls, a: Account) -> "AccountOut":
        return cls(id=a.id, name=a.name, balance=from_cents(a.balance), created_at=a.created_at)


class AccountDeletedOut(BaseModel):
    deleted_account_id: str
    name: str
    final_balance: float
    deleted_transactions: int


class BalanceOut(BaseModel):
    account_id: str
    name: str
    balance: float


class TransactionOut(BaseModel):
    id: str
    account_id: str
    type: TransactionType
    amount: float
    description: str
    reference_id: str
    balance_after: float
    transfer_id: str | None
    created_at: datetime

    _ser_created = field_serializer("created_at")(lambda self, v: _utc(v))

    @classmethod
    def build(cls, t: Transaction) -> "TransactionOut":
        return cls(
            id=t.id,
            account_id=t.account_id,
            type=t.type,
            amount=from_cents(t.amount),
            description=t.description,
            reference_id=t.reference_id,
            balance_after=from_cents(t.balance_after),
            transfer_id=t.transfer_id,
            created_at=t.created_at,
        )


class AccountSummaryOut(BaseModel):
    account_id: str
    name: str
    balance: float
    total_credits: float
    total_debits: float
    credit_count: int
    debit_count: int
    transaction_count: int
    last_transaction_at: datetime | None

    @field_serializer("last_transaction_at")
    def _ser_last(self, v):
        return _utc(v) if v else None


class StatsOut(BaseModel):
    total_accounts: int
    total_balance: float
    total_transactions: int


class Pagination(BaseModel):
    page: int
    limit: int
    total_records: int
    total_pages: int


class TransactionPage(BaseModel):
    data: list[TransactionOut]
    pagination: Pagination


class AccountPage(BaseModel):
    data: list[AccountOut]
    pagination: Pagination


class TransferOut(BaseModel):
    transfer_id: str
    from_account_id: str
    to_account_id: str
    amount: float
    debit_transaction: TransactionOut
    credit_transaction: TransactionOut


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] | dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
