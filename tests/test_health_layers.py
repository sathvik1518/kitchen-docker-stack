"""In-process tests of the two health layers when a dependency is DOWN.
Breaking Redis or Postgres inside the real stack would hurt the other tests, so here the app is pointed at
addresses where nothing is listening."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.settings import Settings, get_settings

DEAD_DB = "postgresql://kitchen:secret-password@127.0.0.1:1/kitchen"
DEAD_REDIS = "redis://:secret-password@127.0.0.1:1/0"


@pytest.fixture()
def client():
    # No `with`: this skips the startup step, which needs a working database.
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def use(settings: Settings):
    app.dependency_overrides[get_settings] = lambda: settings


def test_liveness_stays_ok_even_when_everything_else_is_down(client, settings):
    use(Settings(DEAD_DB, DEAD_REDIS, 30))
    assert client.get("/health").status_code == 200


def test_deep_health_is_ok_when_both_are_up(client, settings):
    use(settings)
    assert client.get("/health/deep").status_code == 200


def test_deep_health_is_503_when_redis_is_down(client, settings):
    use(Settings(settings.database_url, DEAD_REDIS, 30))
    response = client.get("/health/deep")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "ok"
    assert response.json()["checks"]["redis"].startswith("down")


def test_deep_health_is_503_when_the_database_is_down(client, settings):
    use(Settings(DEAD_DB, settings.redis_url, 30))
    response = client.get("/health/deep")
    assert response.status_code == 503
    assert response.json()["checks"]["database"].startswith("down")
    assert response.json()["checks"]["redis"] == "ok"


def test_deep_health_reports_both_when_both_are_down(client):
    use(Settings(DEAD_DB, DEAD_REDIS, 30))
    body = client.get("/health/deep").json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"].startswith("down") and body["checks"]["redis"].startswith("down")


def test_health_answers_never_leak_connection_secrets(client):
    use(Settings(DEAD_DB, DEAD_REDIS, 30))
    assert "secret-password" not in client.get("/health/deep").text


def test_menu_still_works_when_redis_is_down(client, settings):
    use(Settings(settings.database_url, DEAD_REDIS, 30))
    response = client.get("/menu")
    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"
    assert len(response.json()) == 4
