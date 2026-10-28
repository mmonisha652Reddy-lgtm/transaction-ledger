from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.money import from_cents
from app.schemas import ErrorResponse, TransactionOut, TransferCreate, TransferOut
from app.services import ledger

router = APIRouter(prefix="/transfers", tags=["Transfers"])


@router.post(
    "",
    response_model=TransferOut,
    status_code=status.HTTP_201_CREATED,
    summary="Transfer money between two accounts",
    responses={
        400: {"model": ErrorResponse, "description": "Validation error / same account"},
        404: {"model": ErrorResponse, "description": "Account not found"},
        409: {"model": ErrorResponse, "description": "Duplicate reference_id"},
        422: {"model": ErrorResponse, "description": "Insufficient balance"},
    },
)
def create_transfer(body: TransferCreate, db: Session = Depends(get_db)):
    """
    Debits the source and credits the destination in one atomic database transaction.
    Both ledger entries share the same `transfer_id` and `reference_id`.
    If any step fails, nothing is saved.
    """
    transfer_id, debit, credit = ledger.transfer(db, body)
    return TransferOut(
        transfer_id=transfer_id,
        from_account_id=debit.account_id,
        to_account_id=credit.account_id,
        amount=from_cents(debit.amount),
        debit_transaction=TransactionOut.build(debit),
        credit_transaction=TransactionOut.build(credit),
    )
