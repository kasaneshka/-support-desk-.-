"""Проверки корректности на текущем наполнении (сервис должен быть запущен):

    python load/verify.py [--base http://localhost:8000]

1. контрольные суммы таблиц заявок и комментариев (не зависят от часового пояса сервера БД);
2. сводка совпадает с независимым SQL-запросом (число открытых и нарушивших норматив по категориям);
3. постраничный вывод: страницы не пересекаются, total равен числу строк, фильтры по статусу сходятся.
Запускайте сразу после seed.py, до замеров: замер добавляет в базу свои заявки.
"""
import argparse
import json
import os
import sys

import httpx
import psycopg

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8000")
ap.add_argument("--login", default="agent1")
ap.add_argument("--password", default="demo")
args = ap.parse_args()

url = os.environ.get("DATABASE_URL", "postgresql+psycopg://app:changeme@localhost:5432/anzhelika_merdenova").replace("+psycopg", "")
S = os.environ.get("DB_SCHEMA", "anzhelika_merdenova")
fails = []


def check(name, ok, detail=""):
    print(("OK    " if ok else "FAIL  ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


db = psycopg.connect(url)
q = lambda sql, *a: db.execute(sql, a).fetchall()

counts = {t: q(f'SELECT count(*) FROM "{S}".{t}')[0][0] for t in ("users", "categories", "tickets", "comments")}
print("строк в таблицах:", counts)

sum_t = q(f"""SELECT md5(string_agg(concat_ws('|', id, title, status, category_id, author_id,
              extract(epoch FROM created_at)::bigint, extract(epoch FROM updated_at)::bigint), ';' ORDER BY id)) FROM "{S}".tickets""")[0][0]
sum_c = q(f"""SELECT md5(string_agg(concat_ws('|', id, ticket_id, author_id, extract(epoch FROM created_at)::bigint), ';' ORDER BY id))
              FROM "{S}".comments""")[0][0]
print(f"контрольная сумма заявок: {sum_t[:8]}…  комментариев: {sum_c[:8]}…")

c = httpx.Client(base_url=args.base, timeout=600)
assert c.post("/api/login", json={"login": args.login, "password": args.password}).status_code == 200

api = c.get("/api/summary").json()
sql = q(f"""
WITH fr AS (SELECT c.ticket_id, min(c.created_at) t FROM "{S}".comments c
            JOIN "{S}".users u ON u.id = c.author_id WHERE u.role = 'agent' GROUP BY c.ticket_id)
SELECT cat.id, count(*),
       count(*) FILTER (WHERE extract(epoch FROM (coalesce(fr.t, now()) - t.created_at)) / 60 > cat.reaction_norm_minutes)
FROM "{S}".tickets t JOIN "{S}".categories cat ON cat.id = t.category_id
LEFT JOIN fr ON fr.ticket_id = t.id WHERE t.status IN ('new', 'in_progress') GROUP BY cat.id ORDER BY cat.id""")
mine = [(i["category_id"], i["open"], i["violated"]) for i in api["by_category"]]
check("сводка совпадает с независимым SQL по всем категориям", mine == [tuple(r) for r in sql],
      f"открытых {api['open_total']}, нарушено {api['violated_total']} ({api['violated_share'] * 100:.1f} %)")

ids, last = [], None
pages = -(-counts["tickets"] // 100)
for p in range(1, min(pages, 3) + 1):
    last = c.get(f"/api/tickets?page={p}&size=100").json()
    check(f"страница {p}: не больше size записей", len(last["items"]) <= 100)
    ids += [t["id"] for t in last["items"]]
check("страницы не пересекаются", len(ids) == len(set(ids)))
check("total равен числу строк таблицы", last["total"] == counts["tickets"], f"{last['total']}")
for st in ("new", "in_progress", "closed"):
    n_sql = q(f'SELECT count(*) FROM "{S}".tickets WHERE status = %s', st)[0][0]
    check(f"фильтр status={st}", c.get(f"/api/tickets?status={st}&size=1").json()["total"] == n_sql, str(n_sql))

print("ИТОГО:", "все проверки пройдены" if not fails else f"ошибок: {len(fails)}")
print(json.dumps({"counts": counts, "md5_tickets": sum_t[:8], "md5_comments": sum_c[:8], "open_total": api["open_total"],
                  "violated_total": api["violated_total"], "violated_share": api["violated_share"]}, ensure_ascii=False))
sys.exit(1 if fails else 0)
