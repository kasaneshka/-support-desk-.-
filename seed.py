"""Наполнение базы: python seed.py --size small|work

Воспроизводимо: генератор случайных чисел инициализируется константой (SEED),
«текущее время» данных — фиксированная дата ANCHOR. Схема каждый раз
пересоздаётся, поэтому повторный запуск даёт одну и ту же базу.
"""
import argparse
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.initdb import init
from app.security import hash_password

SEED = 42
ANCHOR = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

SIZES = {  # пользователи, из них сотрудники, категории, заявки
    "small": dict(users=50, agents=5, categories=5, tickets=300),
    "work": dict(users=3000, agents=30, categories=10, tickets=75000),
}

CATEGORIES = [  # название, норматив реакции, минут
    ("Авария и недоступность сервиса", 30), ("Доступ и пароли", 60), ("Электронная почта", 120),
    ("Сеть и VPN", 120), ("Оборудование", 240), ("Доступ к данным", 240), ("ПО и установка", 480),
    ("Принтеры и МФУ", 480), ("Заказ оборудования", 1440), ("Консультация", 1440),
]
FEMALE = {"Анна", "Мария", "Елена", "Ольга", "Наталья", "Татьяна", "Юлия", "Ирина", "Светлана", "Виктория"}
FIRST = ["Иван", "Пётр", "Анна", "Мария", "Олег", "Елена", "Сергей", "Ольга", "Дмитрий", "Наталья",
         "Андрей", "Татьяна", "Алексей", "Юлия", "Николай", "Ирина", "Максим", "Светлана", "Артём", "Виктория"]
LAST = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Смирнов", "Попов", "Васильев", "Морозов", "Волков",
        "Соколов", "Лебедев", "Козлов", "Новиков", "Орлов", "Фёдоров", "Михайлов", "Беляев", "Зайцев"]
TOPICS = ["не открывается", "не работает", "медленно работает", "просит пароль", "выдаёт ошибку", "пропал доступ"]
THINGS = ["почта", "VPN", "принтер", "учётная запись", "общая папка", "монитор", "ноутбук", "1С", "Wi-Fi", "CRM"]
TEXTS = ["Добрый день, проблема сохраняется.", "Уточните, пожалуйста, когда это началось.",
         "Попробуйте перезагрузить устройство и повторить.", "Перезагрузка не помогла.",
         "Передал заявку профильному специалисту.", "Спасибо, теперь всё работает.",
         "Приложите, пожалуйста, скриншот ошибки.", "Скриншот во вложении, ошибка та же."]


def generate(size: str):
    cfg = SIZES[size]
    rnd = random.Random(SEED)
    pw = hash_password("demo", salt=bytes(8))  # один хеш на всех: пароль demo

    users = []
    for i in range(1, cfg["users"] + 1):
        is_agent = i <= cfg["agents"]
        login = f"agent{i}" if is_agent else f"customer{i - cfg['agents']}"
        first, last = rnd.choice(FIRST), rnd.choice(LAST)
        if first in FEMALE:
            last += "а"  # Иванов -> Иванова
        users.append((i, login, f"{first} {last}", "agent" if is_agent else "customer", pw))
    agents = [u[0] for u in users if u[3] == "agent"]
    customers = [u[0] for u in users if u[3] == "customer"]

    cats = [(i + 1, n, norm) for i, (n, norm) in enumerate(CATEGORIES[: cfg["categories"]])]
    norms = {c[0]: c[2] for c in cats}

    tickets, comments = [], []
    cid = 0
    for tid in range(1, cfg["tickets"] + 1):
        category = rnd.choice(cats)[0]
        author = rnd.choice(customers)
        r = rnd.random()
        status = "new" if r < 0.06 else "in_progress" if r < 0.20 else "closed"
        created = ANCHOR - timedelta(minutes=rnd.randint(1, 180 * 24 * 60))
        title = f"{rnd.choice(THINGS)} {rnd.choice(TOPICS)}"
        last = created
        if status == "new":
            n_comments, delay = rnd.randint(0, 2), None
        else:
            n_comments = rnd.randint(2, 4)
            # срок первого ответа: примерно четверть заявок не укладывается в норматив
            delay = max(1, int(norms[category] * rnd.choice([0.1, 0.3, 0.5, 0.8, 1.5, 3.0])))
        t = created
        for k in range(n_comments):
            if status == "new":
                who = author  # в новой заявке отвечал только автор
                t += timedelta(minutes=rnd.randint(1, 600))
            elif k == 0:
                who = rnd.choice(agents)
                t = created + timedelta(minutes=delay)
            else:
                who = author if k % 2 else rnd.choice(agents)
                t += timedelta(minutes=rnd.randint(5, 1500))
            cid += 1
            comments.append((cid, tid, who, rnd.choice(TEXTS), t))
            last = t
        tickets.append((tid, title, f"Описание проблемы: {title}.", status, category, author, created, last))
    return users, cats, tickets, comments


def copy_rows(cur, table, columns, rows):
    with cur.copy(f'COPY "{settings.db_schema}".{table} ({columns}) FROM STDIN') as cp:
        for row in rows:
            cp.write_row(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", choices=SIZES, required=True)
    size = ap.parse_args().size

    init(drop=True)
    users, cats, tickets, comments = generate(size)
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        copy_rows(cur, "users", "id, login, name, role, password_hash", users)
        copy_rows(cur, "categories", "id, name, reaction_norm_minutes", cats)
        copy_rows(cur, "tickets", "id, title, body, status, category_id, author_id, created_at, updated_at", tickets)
        copy_rows(cur, "comments", "id, ticket_id, author_id, body, created_at", comments)
        for table in ("users", "categories", "tickets", "comments"):
            cur.execute(f"SELECT setval(pg_get_serial_sequence('\"{settings.db_schema}\".{table}', 'id'), "
                        f'(SELECT max(id) FROM "{settings.db_schema}".{table}))')
        raw.commit()
        cur.execute(f'ANALYZE')  # статистика планировщика
        raw.commit()
    finally:
        raw.close()

    with engine.connect() as conn:
        print(f"наполнение «{size}», схема {settings.db_schema}:")
        for table in ("users", "categories", "tickets", "comments"):
            n = conn.execute(text(f'SELECT count(*) FROM "{settings.db_schema}".{table}')).scalar()
            print(f"  {table:<12}{n:>9}")


if __name__ == "__main__":
    main()
