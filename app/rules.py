"""Бизнес-правила варианта 2 — чистые функции без обращения к БД."""
from datetime import datetime, timedelta

STATUSES = ("new", "in_progress", "closed")
OPEN_STATUSES = ("new", "in_progress")

# заявка ведётся строго по цепочке: новая -> в работе -> закрыта
TRANSITIONS = {
    "new": {"in_progress"},
    "in_progress": {"closed"},
    "closed": set(),
}


def can_transition(old: str, new: str) -> bool:
    return new in TRANSITIONS.get(old, set())


def can_comment(status: str) -> bool:
    """В закрытую заявку комментарии не добавляются."""
    return status != "closed"


def first_agent_reply(comments):
    """Время первого ответа сотрудника.

    comments — пары (created_at, role) в порядке создания.
    Комментарии автора заявки (customer) ответом не считаются.
    """
    for created_at, role in comments:
        if role == "agent":
            return created_at
    return None


def reaction_minutes(created_at: datetime, first_reply_at, now: datetime) -> float:
    """Срок реакции в минутах: до первого ответа, а если ответа нет — до «сейчас»."""
    end = first_reply_at if first_reply_at is not None else now
    return (end - created_at) / timedelta(minutes=1)


def is_norm_violated(created_at: datetime, first_reply_at, norm_minutes: int, now: datetime) -> bool:
    return reaction_minutes(created_at, first_reply_at, now) > norm_minutes
