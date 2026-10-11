"""Подключение к БД и учёт времени, проведённого в запросах.

Для каждого HTTP-запроса ведётся счётчик: сколько запросов выполнено и сколько
миллисекунд заняли вызовы курсора. Итог отдаётся заголовками X-DB-Queries и
X-DB-Time-ms (см. app/main.py). Это «собственные отметки времени вокруг
обращений к базе»: в них входит выполнение запроса и приём ответа от сервера.
"""
import contextvars
import time

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Stats:
    __slots__ = ("queries", "db_ms")

    def __init__(self):
        self.queries = 0
        self.db_ms = 0.0


current_stats: contextvars.ContextVar = contextvars.ContextVar("current_stats", default=None)


class Base(DeclarativeBase):
    metadata = MetaData(schema=settings.db_schema)


engine = create_engine(settings.database_url, pool_size=10, max_overflow=10)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine, "before_cursor_execute")
def _before(conn, cursor, statement, parameters, context, executemany):
    context._t0 = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after(conn, cursor, statement, parameters, context, executemany):
    stats = current_stats.get()
    if stats is not None:
        stats.queries += 1
        stats.db_ms += (time.perf_counter() - context._t0) * 1000


def commit(db):
    """Фиксация транзакции; её время тоже идёт в «время в базе» (запросом не считается)."""
    t0 = time.perf_counter()
    db.commit()
    stats = current_stats.get()
    if stats is not None:
        stats.db_ms += (time.perf_counter() - t0) * 1000


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
