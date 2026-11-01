from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from jobagent.config import get_settings


def database_url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set: copy .env.example to .env and fill it in")
    return url


@lru_cache
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True)


@lru_cache
def _factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine())


def session() -> Session:
    """New session; use as `with session() as s:` or `with session().begin() as s:`."""
    return _factory()()
