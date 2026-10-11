"""Бизнес-правила: без обращения к БД и к работающему сервису."""
from datetime import datetime, timedelta, timezone

from app import rules

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def minutes(n):
    return T0 + timedelta(minutes=n)


def test_new_can_go_to_in_progress():
    assert rules.can_transition("new", "in_progress")


def test_new_cannot_skip_to_closed():
    assert not rules.can_transition("new", "closed")


def test_closed_ticket_cannot_be_reopened():
    assert not rules.can_transition("closed", "in_progress")
    assert not rules.can_transition("closed", "new")


def test_closed_ticket_does_not_accept_comments():
    assert rules.can_comment("in_progress")
    assert not rules.can_comment("closed")


def test_customer_comment_is_not_a_reply():
    comments = [(minutes(5), "customer"), (minutes(40), "agent"), (minutes(50), "agent")]
    assert rules.first_agent_reply(comments) == minutes(40)


def test_no_agent_comments_means_no_reply():
    assert rules.first_agent_reply([(minutes(5), "customer")]) is None


def test_reaction_time_counts_minutes_to_first_reply():
    assert rules.reaction_minutes(T0, minutes(45), now=minutes(1000)) == 45


def test_reply_within_norm_is_not_violation():
    assert not rules.is_norm_violated(T0, minutes(60), norm_minutes=60, now=minutes(1000))


def test_reply_over_norm_is_violation():
    assert rules.is_norm_violated(T0, minutes(61), norm_minutes=60, now=minutes(1000))


def test_unanswered_ticket_violates_norm_after_it_expires():
    assert rules.is_norm_violated(T0, None, norm_minutes=60, now=minutes(61))


def test_unanswered_ticket_within_norm_is_fine():
    assert not rules.is_norm_violated(T0, None, norm_minutes=60, now=minutes(30))
