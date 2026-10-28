from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _on_connect(dbapi_conn, _record):
    # Let SQLAlchemy control transactions (see _on_begin) instead of the
    # sqlite3 driver, and turn on foreign key enforcement.
    dbapi_conn.isolation_level = None
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


@event.listens_for(engine, "begin")
def _on_begin(conn):
    # BEGIN IMMEDIATE takes the write lock at the start of the transaction,
    # so concurrent requests are serialized and a balance can never be read
    # and updated by two requests at the same time.
    conn.exec_driver_sql("BEGIN IMMEDIATE")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Transactions are immutable: the database itself refuses UPDATE, and refuses
# DELETE unless the whole account is being deleted (see models.AccountDeletion).
_TRIGGERS = (
    "DROP TRIGGER IF EXISTS trg_transactions_no_update",
    """
    CREATE TRIGGER trg_transactions_no_update
    BEFORE UPDATE ON transactions
    BEGIN SELECT RAISE(ABORT, 'transactions are immutable'); END
    """,
    "DROP TRIGGER IF EXISTS trg_transactions_no_delete",
    """
    CREATE TRIGGER trg_transactions_no_delete
    BEFORE DELETE ON transactions
    WHEN NOT EXISTS (SELECT 1 FROM account_deletions WHERE account_id = OLD.account_id)
    BEGIN SELECT RAISE(ABORT, 'transactions are immutable'); END
    """,
)


def init_db():
    from app import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "sqlite":
        # Recreated on every start so existing databases get the current rules.
        with engine.begin() as conn:
            for sql in _TRIGGERS:
                conn.exec_driver_sql(sql)
