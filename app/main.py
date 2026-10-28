from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.database import init_db
from app.errors import register_error_handlers
from app.routers import accounts, transfers

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


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(STATIC_DIR / "index.html")
