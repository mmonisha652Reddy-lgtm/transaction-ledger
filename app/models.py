import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_accounts_balance_non_negative"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)  # cents
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "reference_id", name="uq_transactions_account_reference"),
        CheckConstraint("type IN ('credit', 'debit')", name="ck_transactions_type"),
        CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        CheckConstraint("balance_after >= 0", name="ck_transactions_balance_after_non_negative"),
        Index("ix_transactions_account_created", "account_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(6), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # cents
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)  # cents
    transfer_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


# Transactions are immutable: the database itself refuses UPDATE and DELETE,
# not only the API (which simply has no such endpoints).
for _ddl in (
    """
    CREATE TRIGGER IF NOT EXISTS trg_transactions_no_update
    BEFORE UPDATE ON transactions
    BEGIN SELECT RAISE(ABORT, 'transactions are immutable'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_transactions_no_delete
    BEFORE DELETE ON transactions
    BEGIN SELECT RAISE(ABORT, 'transactions are immutable'); END;
    """,
):
    event.listen(Transaction.__table__, "after_create", DDL(_ddl).execute_if(dialect="sqlite"))
