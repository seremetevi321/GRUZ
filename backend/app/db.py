from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from . import config

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, pool_size=10, future=True)


@contextmanager
def tx():
    """Одна бизнес-операция = одна транзакция. Ошибка внутри — полный откат."""
    with engine.begin() as conn:
        yield conn


def read():
    return engine.connect()


__all__ = ["engine", "tx", "read", "Connection"]
