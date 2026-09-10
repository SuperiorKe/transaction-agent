from collections.abc import Iterator
from typing import Any

from sqlalchemy import Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    type_annotation_map = {str: Text}


def make_engine(url: str, **kwargs: Any) -> Engine:
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    engine = create_engine(url, **kwargs)
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(bind: Engine = engine) -> None:
    """Create all tables. P0 has no migrations: schema changes mean deleting the SQLite file."""
    import app.models  # noqa: F401  (registers tables on Base.metadata)

    Base.metadata.create_all(bind)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
