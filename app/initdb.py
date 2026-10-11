"""Создание схемы и таблиц: python -m app.initdb"""
from sqlalchemy import text

from .config import settings
from .db import Base, engine
from . import models  # noqa: F401


def init(drop: bool = False):
    with engine.begin() as conn:
        if drop:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{settings.db_schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"'))
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    init()
    print(f"схема {settings.db_schema} готова")
