import datetime as dt


def test_tasks_requires_auth(client):
    resp = client.get("/tasks/plan", params={"date": "2026-01-01"})
    assert resp.status_code == 401


def test_create_and_list_task(client, auth_headers):
    payload = {
        "title": "Call supplier",
        "estimate_minutes": 20,
        "priority": 2,
    }
    create_resp = client.post("/tasks", json=payload, headers=auth_headers)
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["title"] == "Call supplier"

    day = dt.date.today().isoformat()
    plan_resp = client.get("/tasks/plan", params={"date": day}, headers=auth_headers)
    assert plan_resp.status_code == 200
    data = plan_resp.json()
    assert data["date"] == day
    assert isinstance(data["scheduled"], list)
    assert isinstance(data["backlog"], list)
