"""Операции интерфейса: код ответа, состав полей, авторизация, неверные параметры."""


def test_login_with_wrong_password_is_rejected(client):
    r = client.post("/api/login", json={"login": "agent", "password": "wrong"})
    assert r.status_code == 401


def test_login_without_fields_is_422(client):
    assert client.post("/api/login", json={"login": "agent"}).status_code == 422


def test_list_requires_authorization(client):
    assert client.get("/api/tickets").status_code == 401


def test_list_returns_requested_page(agent_client):
    r = agent_client.get("/api/tickets?page=1&size=3")
    body = r.json()
    assert r.status_code == 200
    assert len(body["items"]) == 3
    assert body["total"] == 4 and body["page"] == 1 and body["size"] == 3
    assert {"id", "title", "status", "category", "author", "created_at"} <= set(body["items"][0])


def test_list_second_page_has_the_rest(agent_client):
    assert len(agent_client.get("/api/tickets?page=2&size=3").json()["items"]) == 1


def test_list_filters_by_status(agent_client):
    body = agent_client.get("/api/tickets?status=in_progress").json()
    assert body["total"] == 2
    assert all(t["status"] == "in_progress" for t in body["items"])


def test_list_rejects_oversized_page(agent_client):
    assert agent_client.get("/api/tickets?size=1000").status_code == 422
    assert agent_client.get("/api/tickets?page=0").status_code == 422
    assert agent_client.get("/api/tickets?status=bogus").status_code == 422


def test_customer_sees_only_own_tickets(alice_client):
    body = alice_client.get("/api/tickets").json()
    assert body["total"] == 2
    assert all(t["author"]["name"] == "Алиса" for t in body["items"])


def test_card_has_comments_and_reaction_fields(agent_client, data):
    card = agent_client.get(f"/api/tickets/{data['work_ok']}").json()
    assert len(card["comments"]) == 2
    assert card["reaction_minutes"] == 30.0          # ответ сотрудника, а не комментарий автора
    assert card["reaction_norm_minutes"] == 60
    assert card["norm_violated"] is False


def test_card_marks_violation(agent_client, data):
    assert agent_client.get(f"/api/tickets/{data['work_bad']}").json()["norm_violated"] is True


def test_card_not_found(agent_client):
    assert agent_client.get("/api/tickets/99999").status_code == 404


def test_customer_cannot_open_foreign_ticket(alice_client, data):
    assert alice_client.get(f"/api/tickets/{data['work_bad']}").status_code == 404


def test_create_ticket(alice_client):
    r = alice_client.post("/api/tickets", json={"title": "Не работает VPN", "body": "ошибка 800", "category_id": 1})
    assert r.status_code == 201
    assert r.json()["status"] == "new" and r.json()["author"]["name"] == "Алиса"


def test_create_ticket_validates_input(alice_client):
    assert alice_client.post("/api/tickets", json={"title": "", "body": "x", "category_id": 1}).status_code == 422
    assert alice_client.post("/api/tickets", json={"title": "t", "body": "x", "category_id": 999}).status_code == 404


def test_create_ticket_requires_authorization(client):
    assert client.post("/api/tickets", json={"title": "t", "body": "x", "category_id": 1}).status_code == 401


def test_status_chain_new_to_closed(agent_client, data):
    tid = data["new"]
    assert agent_client.patch(f"/api/tickets/{tid}/status", json={"status": "in_progress"}).status_code == 200
    assert agent_client.patch(f"/api/tickets/{tid}/status", json={"status": "closed"}).status_code == 200


def test_status_transition_skipping_step_is_409(agent_client, data):
    assert agent_client.patch(f"/api/tickets/{data['new']}/status", json={"status": "closed"}).status_code == 409


def test_status_change_forbidden_for_customer(alice_client, data):
    assert alice_client.patch(f"/api/tickets/{data['new']}/status", json={"status": "in_progress"}).status_code == 403


def test_status_unknown_value_is_422(agent_client, data):
    assert agent_client.patch(f"/api/tickets/{data['new']}/status", json={"status": "weird"}).status_code == 422


def test_add_comment(agent_client, data):
    r = agent_client.post(f"/api/tickets/{data['new']}/comments", json={"body": "Принято в работу"})
    assert r.status_code == 201 and r.json()["author"]["role"] == "agent"
    assert len(agent_client.get(f"/api/tickets/{data['new']}").json()["comments"]) == 1


def test_comment_on_closed_ticket_is_409(agent_client):
    closed_id = agent_client.get("/api/tickets?status=closed").json()["items"][0]["id"]
    assert agent_client.post(f"/api/tickets/{closed_id}/comments", json={"body": "x"}).status_code == 409


def test_summary_counts_open_tickets_and_violations(agent_client):
    s = agent_client.get("/api/summary").json()
    assert s["open_total"] == 3                       # закрытая не считается
    assert s["violated_total"] == 2                   # новая без ответа и «ответили за 500 мин»
    assert s["violated_share"] == round(2 / 3, 4)
    by_name = {c["name"]: c for c in s["by_category"]}
    assert by_name["Срочная"]["open"] == 2 and by_name["Срочная"]["violated"] == 1
    assert by_name["Обычная"]["open"] == 1 and by_name["Обычная"]["violated"] == 1


def test_summary_is_for_agents_only(alice_client, client):
    assert alice_client.get("/api/summary").status_code == 403
    assert client.get("/api/summary").status_code == 401
