from collections.abc import Iterator

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_engine


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = make_engine("sqlite://", poolclass=StaticPool)
    init_db(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s
