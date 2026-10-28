from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.config import DEFAULT_PAGE_SIZE, MAX_PAGE, MAX_PAGE_SIZE
from app.database import get_db
from app.schemas import (
    AccountCreate,
    AccountDeletedOut,
    AccountOut,
    AccountPage,
    AccountSummaryOut,
    BalanceOut,
    ErrorResponse,
    Pagination,
    TransactionCreate,
    TransactionOut,
    TransactionPage,
    TransactionType,
)
from app.money import from_cents
from app.services import ledger

router = APIRouter(prefix="/accounts", tags=["Accounts"])

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Account not found"}}
INVALID = {400: {"model": ErrorResponse, "description": "Validation error"}}


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED, responses=INVALID,
             summary="Create an account")
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    """Creates an account with a system-generated `id` and `created_at`. Balance always starts at 0."""
    return AccountOut.build(ledger.create_account(db, body.name))


@router.get("", response_model=AccountPage, responses=INVALID, summary="List accounts")
def list_accounts(
    page: int = Query(1, ge=1, le=MAX_PAGE),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    rows, total = ledger.list_accounts(db, page, limit)
    return AccountPage(
        data=[AccountOut.build(a) for a in rows],
        pagination=Pagination(page=page, limit=limit, total_records=total,
                              total_pages=-(-total // limit) if total else 0),
    )


@router.get("/{account_id}", response_model=AccountOut, responses=NOT_FOUND, summary="Get an account")
def get_account(account_id: str, db: Session = Depends(get_db)):
    return AccountOut.build(ledger.get_account(db, account_id))


@router.delete("/{account_id}", response_model=AccountDeletedOut, responses=NOT_FOUND,
               summary="Delete an account and all its transactions")
def delete_account(account_id: str, db: Session = Depends(get_db)):
    """
    Permanently deletes the account **and all of its transactions** in one
    atomic database transaction. This cannot be undone.

    Individual transactions still cannot be edited or deleted. For a transfer,
    only this account's side is removed; the other account keeps its entry,
    so its balance stays consistent.
    """
    info = ledger.delete_account(db, account_id)
    return AccountDeletedOut(
        deleted_account_id=info["id"],
        name=info["name"],
        final_balance=from_cents(info["balance"]),
        deleted_transactions=info["deleted_transactions"],
    )


@router.get("/{account_id}/balance", response_model=BalanceOut, responses=NOT_FOUND,
            summary="Get account balance")
def get_balance(account_id: str, db: Session = Depends(get_db)):
    account = ledger.get_account(db, account_id)
    return BalanceOut(account_id=account.id, name=account.name, balance=from_cents(account.balance))


@router.get("/{account_id}/summary", response_model=AccountSummaryOut, responses=NOT_FOUND,
            summary="Get account summary (totals and counts)")
def get_summary(account_id: str, db: Session = Depends(get_db)):
    s = ledger.account_summary(db, account_id)
    a = s["account"]
    return AccountSummaryOut(
        account_id=a.id,
        name=a.name,
        balance=from_cents(a.balance),
        total_credits=from_cents(s["total_credits"]),
        total_debits=from_cents(s["total_debits"]),
        credit_count=s["credit_count"],
        debit_count=s["debit_count"],
        transaction_count=s["credit_count"] + s["debit_count"],
        last_transaction_at=s["last_transaction_at"],
    )


@router.post(
    "/{account_id}/transactions",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a credit or debit transaction",
    responses={
        **INVALID,
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "Duplicate reference_id for this account"},
        422: {"model": ErrorResponse, "description": "Insufficient balance for debit"},
    },
)
def add_transaction(account_id: str, body: TransactionCreate, db: Session = Depends(get_db)):
    """
    - **credit** increases the balance, **debit** decreases it.
    - A debit larger than the balance is rejected (422) and nothing changes.
    - Re-sending a `reference_id` already used on this account returns 409 with the
      existing transaction; no new transaction is created and the balance is unchanged.
    """
    return TransactionOut.build(ledger.add_transaction(db, account_id, body))


@router.get(
    "/{account_id}/transactions",
    response_model=TransactionPage,
    summary="Get transaction history",
    responses={**INVALID, **NOT_FOUND},
)
def list_transactions(
    account_id: str,
    type: TransactionType | None = Query(None, description="Filter by `credit` or `debit`. Omit for both."),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    """Transactions are returned latest first (by `created_at`)."""
    rows, total, total_pages = ledger.list_transactions(db, account_id, type, page, limit)
    return TransactionPage(
        data=[TransactionOut.build(t) for t in rows],
        pagination=Pagination(page=page, limit=limit, total_records=total, total_pages=total_pages),
    )
