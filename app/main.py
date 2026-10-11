import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import rules
from .config import settings
from .db import Stats, commit, current_stats, get_db
from .models import Category, Comment, Ticket, User
from .security import make_token, read_token, verify_password

app = FastAPI(title="Support desk")
STATIC = Path(__file__).parent / "static"


# ---------- замер: заголовки X-Response-Time-ms, X-DB-Time-ms, X-DB-Queries ----------
class TimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        stats = Stats()
        current_stats.set(stats)
        t0 = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-response-time-ms", f"{(time.perf_counter() - t0) * 1000:.2f}".encode()))
                headers.append((b"x-db-time-ms", f"{stats.db_ms:.2f}".encode()))
                headers.append((b"x-db-queries", str(stats.queries).encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(TimingMiddleware)


# ---------- авторизация ----------
def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("session")
    user_id = read_token(token) if token else None
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(401, "Требуется вход")
    return user


def agent_only(user: User = Depends(current_user)) -> User:
    if user.role != "agent":
        raise HTTPException(403, "Только для сотрудников поддержки")
    return user


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=100)


class TicketIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    category_id: int


class StatusIn(BaseModel):
    status: Literal["new", "in_progress", "closed"]


class CommentIn(BaseModel):
    body: str = Field(min_length=1)


def user_dict(u: User) -> dict:
    return {"id": u.id, "name": u.name, "role": u.role}


@app.post("/api/login")
def login(data: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.scalars(select(User).where(User.login == data.login)).first()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Неверный логин или пароль")
    response.set_cookie("session", make_token(user.id), httponly=True, samesite="lax",
                        max_age=settings.session_max_age)
    return {"id": user.id, "login": user.login, "name": user.name, "role": user.role}


@app.post("/api/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie("session")


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "login": user.login, "name": user.name, "role": user.role}


@app.get("/api/categories")
def categories(user: User = Depends(current_user), db: Session = Depends(get_db)):
    cats = db.scalars(select(Category).order_by(Category.id)).all()
    return [{"id": c.id, "name": c.name, "reaction_norm_minutes": c.reaction_norm_minutes} for c in cats]


# ---------- заявки ----------
def ticket_short(t: Ticket) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "status": t.status,
        "category": {"id": t.category.id, "name": t.category.name},
        "author": {"id": t.author.id, "name": t.author.name},
        "created_at": t.created_at.isoformat(),
    }


@app.get("/api/tickets")
def list_tickets(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: Optional[Literal["new", "in_progress", "closed"]] = None,
    category_id: Optional[int] = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    conditions = []
    if user.role == "customer":
        conditions.append(Ticket.author_id == user.id)
    if status:
        conditions.append(Ticket.status == status)
    if category_id:
        conditions.append(Ticket.category_id == category_id)

    total = db.scalar(select(func.count()).select_from(Ticket).where(*conditions))
    tickets = db.scalars(
        select(Ticket).where(*conditions)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .offset((page - 1) * size).limit(size)
    ).all()
    return {"items": [ticket_short(t) for t in tickets], "total": total, "page": page, "size": size}


def load_ticket(db: Session, ticket_id: int, user: User) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or (user.role == "customer" and ticket.author_id != user.id):
        raise HTTPException(404, "Заявка не найдена")
    return ticket


@app.get("/api/tickets/{ticket_id}")
def ticket_card(ticket_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ticket = load_ticket(db, ticket_id, user)
    comments = ticket.comments
    now = datetime.now(timezone.utc)
    first_reply = rules.first_agent_reply([(c.created_at, c.author.role) for c in comments])
    norm = ticket.category.reaction_norm_minutes
    result = ticket_short(ticket)
    result.update({
        "body": ticket.body,
        "updated_at": ticket.updated_at.isoformat(),
        "reaction_norm_minutes": norm,
        "reaction_minutes": round(rules.reaction_minutes(ticket.created_at, first_reply, now), 1),
        "norm_violated": rules.is_norm_violated(ticket.created_at, first_reply, norm, now),
        "comments": [
            {"id": c.id, "author": user_dict(c.author), "body": c.body, "created_at": c.created_at.isoformat()}
            for c in comments
        ],
    })
    return result


@app.post("/api/tickets", status_code=201)
def create_ticket(data: TicketIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    category = db.get(Category, data.category_id)
    if category is None:
        raise HTTPException(404, "Категория не найдена")
    now = datetime.now(timezone.utc)
    ticket = Ticket(title=data.title, body=data.body, status="new", category=category,
                    author=user, created_at=now, updated_at=now)
    db.add(ticket)
    commit(db)
    return ticket_short(ticket)


@app.patch("/api/tickets/{ticket_id}/status")
def change_status(ticket_id: int, data: StatusIn, user: User = Depends(agent_only),
                  db: Session = Depends(get_db)):
    ticket = load_ticket(db, ticket_id, user)
    if not rules.can_transition(ticket.status, data.status):
        raise HTTPException(409, f"Переход {ticket.status} -> {data.status} недопустим")
    ticket.status = data.status
    ticket.updated_at = datetime.now(timezone.utc)
    commit(db)
    return {"id": ticket.id, "status": ticket.status}


@app.post("/api/tickets/{ticket_id}/comments", status_code=201)
def add_comment(ticket_id: int, data: CommentIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    ticket = load_ticket(db, ticket_id, user)
    if not rules.can_comment(ticket.status):
        raise HTTPException(409, "Заявка закрыта")
    now = datetime.now(timezone.utc)
    comment = Comment(ticket=ticket, author=user, body=data.body, created_at=now)
    ticket.updated_at = now
    db.add(comment)
    commit(db)
    return {"id": comment.id, "author": user_dict(user), "body": comment.body,
            "created_at": comment.created_at.isoformat()}


# ---------- сводка ----------
@app.get("/api/summary")
def summary(user: User = Depends(agent_only), db: Session = Depends(get_db)):
    """Открытые заявки по категориям и доля нарушивших норматив срока реакции."""
    now = datetime.now(timezone.utc)
    categories = db.scalars(select(Category).order_by(Category.id)).all()
    open_tickets = db.scalars(select(Ticket).where(Ticket.status.in_(rules.OPEN_STATUSES))).all()

    per_category = {c.id: {"open": 0, "violated": 0} for c in categories}
    norms = {c.id: c.reaction_norm_minutes for c in categories}
    for t in open_tickets:
        first_reply = rules.first_agent_reply([(c.created_at, c.author.role) for c in t.comments])
        row = per_category[t.category_id]
        row["open"] += 1
        if rules.is_norm_violated(t.created_at, first_reply, norms[t.category_id], now):
            row["violated"] += 1

    items = []
    for c in categories:
        row = per_category[c.id]
        items.append({
            "category_id": c.id, "name": c.name, "reaction_norm_minutes": c.reaction_norm_minutes,
            "open": row["open"], "violated": row["violated"],
            "violated_share": round(row["violated"] / row["open"], 4) if row["open"] else 0.0,
        })
    open_total = sum(i["open"] for i in items)
    violated_total = sum(i["violated"] for i in items)
    return {
        "open_total": open_total,
        "violated_total": violated_total,
        "violated_share": round(violated_total / open_total, 4) if open_total else 0.0,
        "by_category": items,
    }


# ---------- клиент ----------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
