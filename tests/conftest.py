import os

# тесты работают в отдельной схеме той же базы — рабочие данные не затрагиваются
os.environ["DB_SCHEMA"] = os.environ.get("TEST_DB_SCHEMA", "anzhelika_merdenova_test")

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.initdb import init  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Category, Comment, Ticket, User  # noqa: E402
from app.security import hash_password  # noqa: E402

NOW = datetime.now(timezone.utc)
PW = hash_password("demo")


@pytest.fixture(scope="session", autouse=True)
def schema():
    init(drop=True)
    yield
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{settings.db_schema}" CASCADE'))


@pytest.fixture()
def data(schema):
    """Небольшой набор данных, пересоздаётся перед каждым тестом."""
    with engine.begin() as conn:
        conn.execute(text(f'TRUNCATE "{settings.db_schema}".comments, "{settings.db_schema}".tickets, '
                          f'"{settings.db_schema}".users, "{settings.db_schema}".categories RESTART IDENTITY CASCADE'))
    db = SessionLocal()
    agent = User(login="agent", name="Агент", role="agent", password_hash=PW)
    alice = User(login="alice", name="Алиса", role="customer", password_hash=PW)
    bob = User(login="bob", name="Боб", role="customer", password_hash=PW)
    fast = Category(name="Срочная", reaction_norm_minutes=60)
    slow = Category(name="Обычная", reaction_norm_minutes=240)
    db.add_all([agent, alice, bob, fast, slow])
    db.flush()

    def ticket(author, cat, status, age_min, title):
        t = Ticket(title=title, body="текст", status=status, category=cat, author=author,
                   created_at=NOW - timedelta(minutes=age_min), updated_at=NOW - timedelta(minutes=age_min))
        db.add(t)
        db.flush()
        return t

    def comment(t, author, minutes_after_create):
        db.add(Comment(ticket=t, author=author, body="к",
                       created_at=t.created_at + timedelta(minutes=minutes_after_create)))

    t_new = ticket(alice, fast, "new", 600, "новая без ответа, давно")        # норматив нарушен
    t_work_ok = ticket(alice, fast, "in_progress", 600, "в работе, ответили за 30 мин")
    comment(t_work_ok, alice, 5)   # реплика автора ответом не считается
    comment(t_work_ok, agent, 30)
    t_work_bad = ticket(bob, slow, "in_progress", 900, "в работе, ответили за 500 мин")
    comment(t_work_bad, agent, 500)
    ticket(bob, slow, "closed", 900, "закрытая")
    db.commit()
    db.close()
    return {"new": t_new.id, "work_ok": t_work_ok.id, "work_bad": t_work_bad.id}


@pytest.fixture()
def client(data):
    return TestClient(app)


def _logged(login):
    c = TestClient(app)
    r = c.post("/api/login", json={"login": login, "password": "demo"})
    assert r.status_code == 200
    return c


@pytest.fixture()
def agent_client(data):
    return _logged("agent")


@pytest.fixture()
def alice_client(data):
    return _logged("alice")
