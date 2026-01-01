from app.rate_limit import reset_rate_limiter
from app.settings import settings


def test_rate_limit_triggers(client, auth_headers):
    reset_rate_limiter()
    prev_limit = settings.API_RATE_LIMIT_PER_MIN
    prev_window = settings.API_RATE_WINDOW_SEC
    settings.API_RATE_LIMIT_PER_MIN = 2
    settings.API_RATE_WINDOW_SEC = 60

    try:
        assert client.get("/tasks/backlog", headers=auth_headers).status_code == 200
        assert client.get("/tasks/backlog", headers=auth_headers).status_code == 200
        resp = client.get("/tasks/backlog", headers=auth_headers)
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
    finally:
        settings.API_RATE_LIMIT_PER_MIN = prev_limit
        settings.API_RATE_WINDOW_SEC = prev_window
        reset_rate_limiter()
