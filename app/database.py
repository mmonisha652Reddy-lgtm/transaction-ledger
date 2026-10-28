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


def init_db():
    from app import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(bind=engine)
