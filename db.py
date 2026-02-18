from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine


def create_db_engine(db_url: str):
    connect_args = {}
    if db_url.startswith("sqlite:"):
        connect_args = {"check_same_thread": False}
    return create_engine(db_url, echo=False, connect_args=connect_args)


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope(engine):
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_dirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)

