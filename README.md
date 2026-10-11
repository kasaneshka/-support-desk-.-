# Служба поддержки: заявки, статусы, срок реакции

Учебный веб-сервис (вариант 2 курсовой работы по дисциплине «Оптимизация клиент-серверных приложений»,
префикс `anzhelika_merdenova`). Принимает заявки с категорией, ведёт их по статусам «новая → в работе → закрыта»,
хранит переписку комментариями, считает срок реакции (от создания заявки до первого ответа сотрудника) и сравнивает
его с нормативом категории. Сводка показывает число открытых заявок по категориям и долю нарушивших норматив.

## Требования

- Python 3.12
- PostgreSQL 16 (подойдёт контейнер из `docker-compose.yml`; нужен Docker Compose v2)
- Версии библиотек — в `requirements.txt` (FastAPI 0.142.2, SQLAlchemy 2.1.3, psycopg 3.3.6, uvicorn 0.54.0)

## Установка и запуск

    git clone <адрес репозитория> && cd support-desk
    cp .env.example .env
    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    docker compose up -d db
    set -a && . ./.env && set +a
    python -m app.initdb
    python seed.py --size small
    python -m uvicorn app.main:app --port $APP_PORT

Рабочий объём данных (пересоздаёт схему целиком; повторный запуск даёт ту же базу):

    python seed.py --size work

Если PostgreSQL уже установлен без Docker: создайте пользователя и базу из `.env` вручную, `docker compose` пропустите.

## Переменные окружения

| Переменная | Назначение | Пример |
|---|---|---|
| PREFIX | личный префикс работы | anzhelika_merdenova |
| DATABASE_URL | подключение к БД (драйвер psycopg 3) | postgresql+psycopg://app:changeme@localhost:5432/anzhelika_merdenova |
| DB_SCHEMA | имя схемы (содержит префикс) | anzhelika_merdenova |
| APP_PORT | порт сервиса | 8000 |
| SECRET_KEY | ключ подписи сессионной куки | changeme |
| POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / POSTGRES_PORT | параметры контейнера БД | app / changeme / anzhelika_merdenova / 5432 |

## Проверка работоспособности

Интерфейс: http://localhost:8000. Учётные записи (пароль у всех `demo`): сотрудник `agent1`
(видит все заявки, меняет статусы, открывает сводку), заказчик `customer1` (видит только свои заявки).
Экраны: список заявок с фильтром и страницами, карточка заявки с комментариями, сводка.

## Тесты

    python -m pytest -q

Тесты работают в отдельной схеме `anzhelika_merdenova_test` той же базы (создаётся и удаляется самими тестами);
рабочие данные не затрагиваются. Нужна запущенная БД и переменные из `.env`.

## Замеры

    python load/measure.py --label small    # после seed.py --size small
    python load/measure.py --label work     # после seed.py --size work

Результаты — в `results/<label>.json` и таблицей в консоли. Время в базе и число запросов сервис отдаёт заголовками
`X-DB-Time-ms` и `X-DB-Queries` (полное время обработчика — `X-Response-Time-ms`). Время в базе — это сумма
интервалов вокруг вызовов курсора и фиксации транзакции; в «код» попадает всё остальное.

## Ограничения первого задания

Индексы только на первичные и внешние ключи (`users.login` уникален — для входа); кеширования, очередей, фоновой
обработки и хранения файлов вне БД нет. Остальные индексы (по статусу, дате и т. п.) — задача третьего задания.

## Программный интерфейс

Авторизация: `POST /api/login` ставит куку `session`; без неё все операции, кроме входа, отвечают 401.
Роли: `agent` — сотрудник поддержки, `customer` — заказчик (видит и комментирует только свои заявки; чужие — 404).
Статусы: `new`, `in_progress`, `closed`; допустимы только переходы `new → in_progress → closed`.

| Метод и путь | Параметры | Ответ | Ошибки |
|---|---|---|---|
| POST /api/login | login, password (JSON) | 200 `{id, login, name, role}` + кука | 401, 422 |
| POST /api/logout | — | 204 | — |
| GET /api/me | — | 200 `{id, login, name, role}` | 401 |
| GET /api/categories | — | 200 `[{id, name, reaction_norm_minutes}]` | 401 |
| GET /api/tickets | page ≥ 1 (1), size 1–100 (20), status, category_id | 200 `{items:[{id, title, status, category{id,name}, author{id,name}, created_at}], total, page, size}` | 401, 422 |
| GET /api/tickets/{id} | — | 200 заявка с `body`, `updated_at`, `comments[]`, `reaction_minutes`, `reaction_norm_minutes`, `norm_violated` | 401, 404 |
| POST /api/tickets | title, body, category_id (JSON) | 201 созданная заявка (статус `new`) | 401, 404 (нет категории), 422 |
| PATCH /api/tickets/{id}/status | status (JSON) | 200 `{id, status}` | 401, 403 (не сотрудник), 404, 409 (недопустимый переход), 422 |
| POST /api/tickets/{id}/comments | body (JSON) | 201 комментарий `{id, author, body, created_at}` | 401, 404, 409 (заявка закрыта), 422 |
| GET /api/summary | — (только сотрудник) | 200 `{open_total, violated_total, violated_share, by_category:[{category_id, name, reaction_norm_minutes, open, violated, violated_share}]}` | 401, 403 |

Правила срока реакции: ответом считается первый комментарий сотрудника (комментарии заказчика не считаются);
если ответа нет, срок идёт до текущего момента. Норматив нарушен, если срок больше `reaction_norm_minutes` категории.
Сводка считает только открытые заявки (`new`, `in_progress`).
