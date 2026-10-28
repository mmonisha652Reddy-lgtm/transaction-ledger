from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db, init_db
from app.errors import register_error_handlers
from app.money import from_cents
from app.routers import accounts, transfers
from app.schemas import StatsOut
from app.services import ledger

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Transaction Ledger API",
    version="1.0.0",
    description="Accounts, credit/debit transactions, balances, history and transfers.",
    lifespan=lifespan,
)

register_error_handlers(app)
app.include_router(accounts.router)
app.include_router(transfers.router)


@app.get("/health", tags=["System"], summary="Health check")
def health():
    return {"status": "ok"}


@app.get("/stats", response_model=StatsOut, tags=["System"], summary="Overall ledger stats")
def stats(db: Session = Depends(get_db)):
    s = ledger.ledger_stats(db)
    return StatsOut(
        total_accounts=s["total_accounts"],
        total_balance=from_cents(s["total_balance"]),
        total_transactions=s["total_transactions"],
    )


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(STATIC_DIR / "index.html")
