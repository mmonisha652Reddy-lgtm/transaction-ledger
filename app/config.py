import os

# SQLite file in the project folder by default. Override with the DATABASE_URL env var.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ledger.db")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
# Keeps page * limit well inside the database's 64-bit integer range.
MAX_PAGE = 1_000_000_000
