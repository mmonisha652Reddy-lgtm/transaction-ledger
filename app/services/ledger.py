"""Ledger business logic.

Every write runs inside a single database transaction (`with db.begin()`):
if anything fails, the whole unit is rolled back, so a transaction row is
never saved without its balance change (and a transfer is never half done).
"""
import math
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AccountNotFound, DuplicateTransaction, InsufficientBalance, SameAccountTransfer
from app.models import Account, Transaction
from app.money import from_cents, to_cents
from app.schemas import TransactionCreate, TransactionOut, TransactionType, TransferCreate


# ---------- accounts ----------

def create_account(db: Session, name: str) -> Account:
    with db.begin():
        account = Account(name=name, balance=0)  # balance is never taken from the client
        db.add(account)
    return account


def get_account(db: Session, account_id: str) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise AccountNotFound(account_id)
    return account


def list_accounts(db: Session, page: int, limit: int) -> tuple[list[Account], int]:
    total = db.scalar(select(func.count()).select_from(Account))
    rows = db.scalars(
        select(Account).order_by(Account.created_at.desc()).offset((page - 1) * limit).limit(limit)
    ).all()
    return list(rows), total


# ---------- transactions ----------

def _find_by_reference(db: Session, account_id: str, reference_id: str) -> Transaction | None:
    return db.scalar(
        select(Transaction).where(
            Transaction.account_id == account_id, Transaction.reference_id == reference_id
        )
    )


def _raise_duplicate(existing: Transaction) -> None:
    raise DuplicateTransaction(
        f"Duplicate transaction. Reference ID '{existing.reference_id}' has already been used for this account.",
        {"existing_transaction": TransactionOut.build(existing).model_dump(mode="json")},
    )


def _post(
    db: Session,
    account: Account,
    tx_type: TransactionType,
    amount_cents: int,
    description: str,
    reference_id: str,
    transfer_id: str | None = None,
) -> Transaction:
    """Apply one credit/debit to an account. Must be called inside an open DB transaction."""
    existing = _find_by_reference(db, account.id, reference_id)
    if existing is not None:
        _raise_duplicate(existing)

    if tx_type == TransactionType.debit:
        if account.balance < amount_cents:
            raise InsufficientBalance(
                f"Insufficient balance. Available balance is {from_cents(account.balance):,.2f}, "
                f"but {from_cents(amount_cents):,.2f} was requested.",
                {
                    "account_id": account.id,
                    "balance": from_cents(account.balance),
                    "requested_amount": from_cents(amount_cents),
                },
            )
        account.balance -= amount_cents
    else:
        account.balance += amount_cents

    txn = Transaction(
        account_id=account.id,
        type=tx_type.value,
        amount=amount_cents,
        description=description,
        reference_id=reference_id,
        balance_after=account.balance,
        transfer_id=transfer_id,
    )
    db.add(txn)
    db.flush()  # surfaces constraint violations now, while we can still roll back
    return txn


def add_transaction(db: Session, account_id: str, data: TransactionCreate) -> Transaction:
    try:
        with db.begin():
            account = get_account(db, account_id)
            return _post(db, account, data.type, to_cents(data.amount), data.description, data.reference_id)
    except IntegrityError:
        # Safety net: the unique (account_id, reference_id) constraint caught a duplicate.
        existing = _find_by_reference(db, account_id, data.reference_id)
        if existing is not None:
            _raise_duplicate(existing)
        raise


def list_transactions(
    db: Session, account_id: str, tx_type: TransactionType | None, page: int, limit: int
) -> tuple[list[Transaction], int, int]:
    get_account(db, account_id)

    conditions = [Transaction.account_id == account_id]
    if tx_type is not None:
        conditions.append(Transaction.type == tx_type.value)

    total = db.scalar(select(func.count()).select_from(Transaction).where(*conditions))
    rows = db.scalars(
        select(Transaction)
        .where(*conditions)
        # latest first; rowid breaks ties between rows created in the same instant
        .order_by(Transaction.created_at.desc(), text("transactions.rowid DESC"))
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()
    total_pages = math.ceil(total / limit) if total else 0
    return list(rows), total, total_pages


# ---------- insights ----------

def account_summary(db: Session, account_id: str) -> dict:
    account = get_account(db, account_id)
    rows = db.execute(
        select(Transaction.type, func.count(), func.coalesce(func.sum(Transaction.amount), 0))
        .where(Transaction.account_id == account_id)
        .group_by(Transaction.type)
    ).all()
    by_type = {t: (count, total) for t, count, total in rows}
    credit_count, credit_total = by_type.get("credit", (0, 0))
    debit_count, debit_total = by_type.get("debit", (0, 0))
    last_at = db.scalar(select(func.max(Transaction.created_at)).where(Transaction.account_id == account_id))
    return {
        "account": account,
        "total_credits": credit_total,
        "total_debits": debit_total,
        "credit_count": credit_count,
        "debit_count": debit_count,
        "last_transaction_at": last_at,
    }


def ledger_stats(db: Session) -> dict:
    accounts, balance = db.execute(
        select(func.count(), func.coalesce(func.sum(Account.balance), 0)).select_from(Account)
    ).one()
    transactions = db.scalar(select(func.count()).select_from(Transaction))
    return {"total_accounts": accounts, "total_balance": balance, "total_transactions": transactions}


# ---------- transfers ----------

def transfer(db: Session, data: TransferCreate) -> tuple[str, Transaction, Transaction]:
    if data.from_account_id == data.to_account_id:
        raise SameAccountTransfer("Source and destination accounts must be different.")

    amount_cents = to_cents(data.amount)
    transfer_id = str(uuid.uuid4())
    description = data.description or "Transfer"

    try:
        with db.begin():
            source = get_account(db, data.from_account_id)
            dest = get_account(db, data.to_account_id)
            debit = _post(
                db, source, TransactionType.debit, amount_cents,
                description, data.reference_id, transfer_id,
            )
            credit = _post(
                db, dest, TransactionType.credit, amount_cents,
                description, data.reference_id, transfer_id,
            )
            return transfer_id, debit, credit
    except IntegrityError:
        for acc_id in (data.from_account_id, data.to_account_id):
            existing = _find_by_reference(db, acc_id, data.reference_id)
            if existing is not None:
                _raise_duplicate(existing)
        raise

