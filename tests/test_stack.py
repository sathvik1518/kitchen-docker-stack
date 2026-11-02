"""Regression tests against the REAL running stack: api + Postgres + Redis, over real HTTP."""
import json

import pytest

MENU_KEY = "menu:v1"


# ---------- Health and docs ----------


def test_liveness_is_ok(api):
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_deep_health_checks_database_and_redis(api):
    response = api.get("/health/deep")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


def test_api_docs_page_is_served(api):
    response = api.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_openapi_lists_every_endpoint(api):
    paths = api.get("/openapi.json").json()["paths"]
    assert set(paths) == {"/health", "/health/deep", "/menu", "/orders", "/orders/{order_id}"}


# ---------- Menu and the Redis cache ----------


def test_menu_comes_from_the_database(api, redis_client):
    redis_client.delete(MENU_KEY)
    response = api.get("/menu")
    assert response.status_code == 200
    assert response.json() == [
        {"id": 1, "name": "Idli", "price": 40},
        {"id": 2, "name": "Dosa", "price": 70},
        {"id": 3, "name": "Pongal", "price": 60},
        {"id": 4, "name": "Filter Coffee", "price": 30},
    ]


def test_second_menu_request_comes_from_redis(api, redis_client):
    redis_client.delete(MENU_KEY)
    first = api.get("/menu")
    second = api.get("/menu")
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert second.json() == first.json()


def test_menu_is_really_saved_in_redis_with_an_expiry(api, redis_client):
    redis_client.delete(MENU_KEY)
    api.get("/menu")
    saved = redis_client.get(MENU_KEY)
    assert saved is not None
    assert [item["name"] for item in json.loads(saved)][0] == "Idli"
    assert 0 < redis_client.ttl(MENU_KEY) <= 30


# ---------- Orders ----------


def test_place_an_order_and_read_it_back(api):
    created = api.post("/orders", json={"items": [{"menu_id": 1, "qty": 2}, {"menu_id": 2, "qty": 1}]})
    assert created.status_code == 201
    body = created.json()
    assert body["total"] == 150  # 2 x Idli (40) + 1 x Dosa (70)

    fetched = api.get(f"/orders/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["total"] == 150
    assert fetched.json()["items"] == [{"menu_id": 1, "qty": 2}, {"menu_id": 2, "qty": 1}]


def test_each_order_gets_its_own_id(api):
    one = api.post("/orders", json={"items": [{"menu_id": 4, "qty": 1}]}).json()["id"]
    two = api.post("/orders", json={"items": [{"menu_id": 4, "qty": 1}]}).json()["id"]
    assert one != two


def test_the_server_uses_its_own_prices_not_the_clients(api):
    response = api.post("/orders", json={"items": [{"menu_id": 1, "qty": 1, "price": 1}]})
    assert response.status_code == 201
    assert response.json()["total"] == 40


def test_unknown_order_is_404(api):
    response = api.get("/orders/99999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Order not found."}


def test_unknown_menu_item_is_rejected_and_nothing_is_saved(api):
    response = api.post("/orders", json={"items": [{"menu_id": 999, "qty": 1}]})
    assert response.status_code == 422
    assert "999" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"items": []},
        {"items": [{"menu_id": 1, "qty": 0}]},
        {"items": [{"menu_id": 1, "qty": 11}]},
        {"items": [{"menu_id": 0, "qty": 1}]},
        {"items": [{"menu_id": 1, "qty": "two"}]},
        {"items": [{"qty": 1}]},
        {},
    ],
    ids=["no items", "qty 0", "qty 11", "menu id 0", "qty not a number", "no menu id", "empty body"],
)
def test_bad_orders_are_rejected(api, payload):
    assert api.post("/orders", json=payload).status_code == 422
