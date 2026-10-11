"""Замер времени ответа операций.

    python load/measure.py --label small    # на малом наполнении
    python load/measure.py --label work     # на рабочем наполнении

Для каждой операции: один прогревочный запрос (в расчёт не входит), затем
--repeats повторов (по умолчанию 20) под учётной записью agent1. Время ответа —
по часам клиента вокруг запроса; из заголовков ответа берутся время в базе
(X-DB-Time-ms) и число запросов к базе (X-DB-Queries) за одно выполнение.
p95 — по правилу ближайшего ранга: при 20 повторах это 19-е по возрастанию.
"""
import argparse
import json
import math
import os
import platform
import statistics
import time

import httpx
import psycopg


def p(values, q):
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def environment(db_url):
    cpu, cores = platform.processor() or "?", os.cpu_count()
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
        mem = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemTotal"))
        mem_gb = round(mem / 1024 / 1024, 1)
    except OSError:
        mem_gb = None
    with psycopg.connect(db_url) as conn:
        pg = conn.execute("SHOW server_version").fetchone()[0]
    return {"cpu": cpu, "cores": cores, "memory_gb": mem_gb, "python": platform.python_version(),
            "postgres": pg, "os": platform.platform()}


def counts(db_url, schema):
    with psycopg.connect(db_url) as conn:
        return {t: conn.execute(f'SELECT count(*) FROM "{schema}".{t}').fetchone()[0]
                for t in ("users", "categories", "tickets", "comments")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--repeats", type=int, default=20)
    ap.add_argument("--login", default="agent1")
    ap.add_argument("--password", default="demo")
    ap.add_argument("--card-id", type=int, default=150)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL", "postgresql+psycopg://app:changeme@localhost:5432/anzhelika_merdenova")
    db_url = db_url.replace("+psycopg", "")
    schema = os.environ.get("DB_SCHEMA", "anzhelika_merdenova")
    before = counts(db_url, schema)

    client = httpx.Client(base_url=args.base, timeout=120)
    r = client.post("/api/login", json={"login": args.login, "password": args.password})
    assert r.status_code == 200, r.text
    anon = httpx.Client(base_url=args.base, timeout=120)  # без куки: для входа и выхода
    created = []  # заявки, созданные самим замером; на них же замеряются комментарий и смена статуса

    def create(i):
        return client.post("/api/tickets", json={"title": f"Замер {i}", "body": "нагрузочная заявка", "category_id": 1})

    def op_login(i):
        return anon.post("/api/login", json={"login": args.login, "password": args.password})

    ops = [
        ("POST /api/login", op_login),
        ("GET /api/me", lambda i: client.get("/api/me")),
        ("POST /api/logout", lambda i: anon.post("/api/logout")),
        ("GET /api/categories", lambda i: client.get("/api/categories")),
        ("GET /api/tickets (стр. 1, 20 шт.)", lambda i: client.get("/api/tickets?page=1&size=20")),
        ("GET /api/tickets (status=new)", lambda i: client.get("/api/tickets?page=1&size=20&status=new")),
        ("GET /api/tickets (стр. 10)", lambda i: client.get("/api/tickets?page=10&size=20")),
        ("GET /api/tickets/{id}", lambda i: client.get(f"/api/tickets/{args.card_id}")),
        ("GET /api/summary", lambda i: client.get("/api/summary")),
        ("POST /api/tickets", create),
        ("POST /api/tickets/{id}/comments",
         lambda i: client.post(f"/api/tickets/{created[i]}/comments", json={"body": "Принято в работу"})),
        ("PATCH /api/tickets/{id}/status",
         lambda i: client.patch(f"/api/tickets/{created[i]}/status", json={"status": "in_progress"})),
    ]

    results = []
    for name, fn in ops:
        samples = []
        for i in range(args.repeats + 1):  # i = 0 — прогрев
            t0 = time.perf_counter()
            resp = fn(i)
            elapsed = (time.perf_counter() - t0) * 1000
            assert resp.status_code in (200, 201, 204), (name, resp.status_code, resp.text[:200])
            if name == "POST /api/tickets":
                created.append(resp.json()["id"])
            if i == 0:
                continue
            h = resp.headers
            samples.append({"total": elapsed, "server": float(h["x-response-time-ms"]),
                            "db": float(h["x-db-time-ms"]), "queries": int(h["x-db-queries"]),
                            "bytes": len(resp.content)})
        tot = [s["total"] for s in samples]
        server = statistics.median(s["server"] for s in samples)
        db = statistics.median(s["db"] for s in samples)
        results.append({
            "operation": name, "p50": statistics.median(tot), "p95": p(tot, 0.95), "max": max(tot),
            "server_ms": server, "db_ms": db, "code_ms": server - db,
            "queries": statistics.median(s["queries"] for s in samples),
            "bytes": samples[-1]["bytes"],
        })

    after = counts(db_url, schema)
    out = {"label": args.label, "repeats": args.repeats, "account": args.login, "env": environment(db_url),
           "counts_before": before, "counts_after": after, "results": results}
    path = args.out or f"results/{args.label}.json"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=2)

    print(f"условия: {args.base}, учётная запись {args.login}, повторов {args.repeats} (плюс прогрев), наполнение {args.label}")
    print(f"{'операция':<36}{'p50':>9}{'p95':>9}{'макс':>9}{'сервер':>9}{'в базе':>9}{'в коде':>9}{'запр.':>7}")
    for r in results:
        print(f"{r['operation']:<36}{r['p50']:>9.1f}{r['p95']:>9.1f}{r['max']:>9.1f}"
              f"{r['server_ms']:>9.1f}{r['db_ms']:>9.1f}{r['code_ms']:>9.1f}{r['queries']:>7.0f}")
    print("добавлено замером:", {k: after[k] - before[k] for k in after if after[k] != before[k]})


if __name__ == "__main__":
    main()
