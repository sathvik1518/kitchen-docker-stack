# Kitchen Docker Stack: API, Database, Redis and a Test Gate

**Day 20 Mini Capstone, Docker**  
**Project folder:** `kitchen-docker-stack`

---

## 1. Goal

The Kitchen App needed to run the same way on any machine. The task (Day 20 Deep Dive, Mini Capstone) asked for:

1. A **multi-stage Dockerfile**.
2. A **docker-compose.yml** with `api`, `db`, `redis` and `tests` services.
3. **Layered health checks.**
4. A **README** covering setup and the API docs URL.
5. A **regression suite** that runs automatically against the real stack and **blocks a deploy** if anything fails.

Think of a food truck. The Dockerfile is the packing list for the truck: only what it needs on the road, not the whole workshop (multi-stage). Compose is the plan for the whole street stall: the truck, the fridge (database) and the ice box (Redis), all started in the right order. The health checks are two questions: "is the cook awake?" (liveness) and "can the cook actually serve food right now, is the fridge cold, is the ice box working?" (deep health). The deploy gate is the inspector who must say yes before the truck is allowed to open.

The app is a small FastAPI kitchen: a **menu** read from Postgres and cached in Redis, and **orders** saved in Postgres.

---

## 2. Tools

| Tool | Role |
|---|---|
| Python 3.13 + FastAPI | The API and its automatic docs page (`/docs`) |
| PostgreSQL 16 | Stores the menu and the orders |
| Redis 7 | Caches the menu for 30 seconds |
| psycopg 3 and redis-py | Talk to Postgres and Redis |
| Docker and Docker Compose | Package and start the whole stack |
| pytest + httpx | Tests over real HTTP against the running stack |
| GitHub Actions | Runs the same test gate on every push |

---

## 3. Project structure

```
kitchen-docker-stack/
├── Dockerfile                (3 stages: builder, runtime, test)
├── docker-compose.yml        (api, db, redis, tests)
├── .dockerignore
├── pytest.ini                (tells pytest where the app code is)
├── .gitignore
├── requirements.txt          (what the app needs)
├── requirements-dev.txt      (plus what the tests need)
├── README.md
├── DOCUMENTATION.md
├── app/
│   ├── __init__.py
│   ├── settings.py           (all settings come from environment variables)
│   ├── db.py                 (connect, create tables, first menu items)
│   ├── cache.py              (Redis client with short timeouts)
│   ├── health.py             (the real database and Redis checks)
│   └── main.py               (the endpoints)
├── tests/
│   ├── conftest.py           (the client for the real api, Redis client)
│   ├── test_stack.py         (19 tests against the running stack)
│   └── test_health_layers.py (7 tests with a dependency down)
├── scripts/
│   └── deploy.sh             (the deploy gate)
└── .github/workflows/
    └── ci.yml                (same gate on GitHub)
```

---

## 4. Setup commands

```bash
mkdir kitchen-docker-stack
cd kitchen-docker-stack
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install fastapi uvicorn "psycopg[binary]" redis
pip install pytest httpx
```

**`package.json` scripts**

```json
Run the whole stack:        docker compose up --build
Run the tests (real stack): docker compose --profile test run --rm --build tests
Deploy gate:                bash scripts/deploy.sh
```

---

## 5. The idea: the stack and the two health layers

```
                      docker compose up
   ┌─────────┐      ┌──────────────────────────────────────────┐
   │ browser │ ───▶ │ api  (port 8000)                           │
   └─────────┘      │   GET /menu ──▶ Redis (cache, 30 s)        │
                    │            └──▶ Postgres (if not cached)   │
                    │   POST /orders ──▶ Postgres                │
                    └──────────────┬───────────────┬────────────┘
                                   │               │
                              ┌────▼───┐      ┌────▼───┐
                              │   db   │      │ redis  │
                              └────────┘      └────────┘
   tests service (profile "test") ── real HTTP ──▶ api   (starts only after api is healthy)
```

| Layer | Where | Question | Checks | Down database means |
|---|---|---|---|---|
| 1. Liveness | `GET /health`, the Dockerfile `HEALTHCHECK` | Is the process alive? | Nothing else | Still **200** (restarting the container would not fix the database) |
| 2. Deep | `GET /health/deep`, the compose `healthcheck` for `api` | Can it really work? | Asks Postgres `SELECT 1` and Redis `PING` | **503**, with which one is down |

**Why two layers.** If the one check also asked the database, a database outage would make Docker think the app is dead and restart it again and again, which fixes nothing. Layer 1 answers "restart me?", layer 2 answers "send me traffic / start the tests?".

**Why multi-stage.** The `builder` stage downloads and prepares the packages. The `runtime` stage copies only the prepared packages, runs as a normal user (not root), and ships none of the build tools. The `test` stage is the runtime image plus pytest and the tests, used only by the `tests` service.

**The deploy gate.** `scripts/deploy.sh` runs the tests against the real stack. If they fail, the script exits with an error and prints `BLOCKED`, so the deploy command after it never runs. `ci.yml` does the same on GitHub: a failing test fails the job.

---

## 6. The code

### `requirements.txt`

```text
fastapi==0.162.0
uvicorn==0.54.0
psycopg[binary]==3.3.6
redis==8.1.0
```

### `requirements-dev.txt`

```text
-r requirements.txt
pytest==9.1.1
httpx==0.28.1
```

### `app/settings.py`

```python
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Everything the app needs from the outside world. It all comes from environment variables."""

    database_url: str
    redis_url: str
    menu_cache_seconds: int


def get_settings() -> Settings:
    # Read on every call (not once at import) so tests can change the environment.
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql://kitchen:kitchen@localhost:5432/kitchen"),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        menu_cache_seconds=int(os.environ.get("MENU_CACHE_SECONDS", "30")),
    )
```

### `app/db.py`

```python
import time

import psycopg

SEED_MENU = [
    ("Idli", 40),
    ("Dosa", 70),
    ("Pongal", 60),
    ("Filter Coffee", 30),
]


def connect(url: str) -> psycopg.Connection:
    # A short timeout, so a dead database means a quick error, not a hanging request.
    return psycopg.connect(url, connect_timeout=2)


def init_db(url: str, tries: int = 30, wait_seconds: float = 1.0) -> None:
    """Creates the tables and the first menu items. Retries while the database is still starting."""
    last_error: Exception | None = None
    for _ in range(tries):
        try:
            with connect(url) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS menu_items (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        price INTEGER NOT NULL CHECK (price > 0)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS orders (
                        id SERIAL PRIMARY KEY,
                        total INTEGER NOT NULL,
                        items JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                for name, price in SEED_MENU:
                    conn.execute(
                        "INSERT INTO menu_items (name, price) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                        (name, price),
                    )
            return
        except psycopg.OperationalError as error:
            last_error = error
            time.sleep(wait_seconds)
    raise RuntimeError(f"Database not reachable after {tries} tries: {last_error}")
```

### `app/cache.py`

```python
import redis


def client(url: str) -> redis.Redis:
    # Short timeouts: a dead Redis must not make every request hang.
    return redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)
```

### `app/health.py`

```python
from app import cache, db
from app.settings import Settings


def check_database(settings: Settings) -> str:
    """Asks the database a real question. Returns "ok" or "down: <reason>"."""
    try:
        with db.connect(settings.database_url) as conn:
            conn.execute("SELECT 1")
        return "ok"
    except Exception as error:  # any failure means "not usable"
        return f"down: {type(error).__name__}"


def check_redis(settings: Settings) -> str:
    try:
        cache.client(settings.redis_url).ping()
        return "ok"
    except Exception as error:
        return f"down: {type(error).__name__}"
```

### `app/main.py` (the endpoints, the heart of the capstone)

```python
import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import cache, db, health
from app.settings import Settings, get_settings

MENU_CACHE_KEY = "menu:v1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db(get_settings().database_url)
    yield


app = FastAPI(title="Kitchen API", version="1.0.0", lifespan=lifespan)


# ---------- Health: two layers ----------


@app.get("/health", tags=["health"])
def liveness():
    """Layer 1. "Is the process alive?" It checks nothing else, so it stays 200 even if the database is down."""
    return {"status": "ok"}


@app.get("/health/deep", tags=["health"])
def readiness(settings: Settings = Depends(get_settings)):
    """Layer 2. "Can it really work?" Asks the database and Redis a real question. 503 if either is down."""
    checks = {"database": health.check_database(settings), "redis": health.check_redis(settings)}
    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "unavailable", "checks": checks},
    )


# ---------- Menu (database, cached in Redis) ----------


@app.get("/menu", tags=["menu"])
def get_menu(response: Response, settings: Settings = Depends(get_settings)):
    """The menu. The first request reads the database and saves the answer in Redis; later ones come from Redis."""
    try:
        r = cache.client(settings.redis_url)
        cached = r.get(MENU_CACHE_KEY)
        if cached is not None:
            response.headers["X-Cache"] = "HIT"
            return json.loads(cached)
    except Exception:
        r = None  # Redis is down: the menu still works, just without the cache

    with db.connect(settings.database_url) as conn:
        rows = conn.execute("SELECT id, name, price FROM menu_items ORDER BY id").fetchall()
    items = [{"id": row[0], "name": row[1], "price": row[2]} for row in rows]

    response.headers["X-Cache"] = "MISS"
    if r is not None:
        try:
            r.set(MENU_CACHE_KEY, json.dumps(items), ex=settings.menu_cache_seconds)
        except Exception:
            pass
    return items


# ---------- Orders (database) ----------


class OrderLine(BaseModel):
    menu_id: int = Field(gt=0)
    qty: int = Field(ge=1, le=10)


class OrderIn(BaseModel):
    items: list[OrderLine] = Field(min_length=1, max_length=20)


@app.post("/orders", status_code=201, tags=["orders"])
def place_order(order: OrderIn, settings: Settings = Depends(get_settings)):
    with db.connect(settings.database_url) as conn:
        prices = dict(conn.execute("SELECT id, price FROM menu_items").fetchall())
        unknown = sorted({line.menu_id for line in order.items if line.menu_id not in prices})
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown menu item id(s): {unknown}")
        total = sum(prices[line.menu_id] * line.qty for line in order.items)
        items_json = json.dumps([line.model_dump() for line in order.items])
        row = conn.execute(
            "INSERT INTO orders (total, items) VALUES (%s, %s::jsonb) RETURNING id",
            (total, items_json),
        ).fetchone()
    return {"id": row[0], "total": total, "items": [line.model_dump() for line in order.items]}


@app.get("/orders/{order_id}", tags=["orders"])
def get_order(order_id: int, settings: Settings = Depends(get_settings)):
    with db.connect(settings.database_url) as conn:
        row = conn.execute("SELECT id, total, items FROM orders WHERE id = %s", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    return {"id": row[0], "total": row[1], "items": row[2]}
```

### `Dockerfile` (multi-stage)

```dockerfile
# ---------- Stage 1: builder ----------
# Has the tools needed to download and prepare the packages. None of this ships in the final image.
FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------- Stage 2: runtime (the image that gets deployed) ----------
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# Install only from the prepared wheels, and nothing else.
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

# Run as a normal user, not root.
RUN useradd --create-home --uid 10001 kitchen
COPY app ./app
USER kitchen

EXPOSE 8000
# Layer 1 health check: is the process alive? (Python is already here, so no curl is needed.)
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------- Stage 3: test (only used by the tests service) ----------
FROM runtime AS test
# The app folder is not writable for the normal user, so do not try to write pytest's cache there.
ENV PYTEST_ADDOPTS="-p no:cacheprovider"
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini ./pytest.ini
COPY tests ./tests
USER kitchen
CMD ["pytest", "-v"]
```

### `pytest.ini` (import path for the tests)

```text
[pytest]
# Lets tests do "from app.main import app" no matter where pytest is started from.
# Without this, pytest only adds the tests folder to the import path and "app" is not found.
pythonpath = .
testpaths = tests
```

### `docker-compose.yml`

```yaml
# Starts the whole kitchen: api + db + redis. The tests service is only started on purpose (profile "test").
#   docker compose up --build                      -> run the app
#   docker compose --profile test run --rm tests   -> run the tests against the real stack
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: kitchen
      POSTGRES_PASSWORD: kitchen # for local development only. Never reuse this in production.
      POSTGRES_DB: kitchen
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U kitchen -d kitchen"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build:
      context: .
      target: runtime
    environment:
      DATABASE_URL: postgresql://kitchen:kitchen@db:5432/kitchen
      REDIS_URL: redis://redis:6379/0
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    # Layer 2 health check: can it really work? Asks /health/deep, which asks the database and Redis.
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/deep', timeout=2)"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 5s

  tests:
    profiles: ["test"]
    build:
      context: .
      target: test
    environment:
      API_URL: http://api:8000
      DATABASE_URL: postgresql://kitchen:kitchen@db:5432/kitchen
      REDIS_URL: redis://redis:6379/0
    depends_on:
      api:
        condition: service_healthy

volumes:
  pgdata:
```

### `scripts/deploy.sh` (the deploy gate)

```bash
#!/usr/bin/env bash
# The deploy gate: tests run against the REAL stack first. If anything fails, this script stops and nothing is deployed.
set -euo pipefail

cleanup() { docker compose --profile test down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> Building and running the regression tests against the real stack..."
if ! docker compose --profile test run --rm --build tests; then
  echo "BLOCKED: tests failed. Nothing was deployed."
  exit 1
fi

echo "==> Tests passed. Safe to deploy."
# Put your real deploy command here, for example:
#   docker push your-registry/kitchen-api:latest
```

### `.github/workflows/ci.yml`

```yaml
name: ci
on: [push, pull_request]

jobs:
  regression:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # If any test fails, this step fails, the job fails, and the deploy is blocked.
      - name: Run the regression tests against the real stack
        run: docker compose --profile test run --rm --build tests
```

### `.dockerignore`

```text
.git
.github
.venv
__pycache__
*.pyc
.pytest_cache
.env
README.md
DOCUMENTATION.md
```

### `README.md`

```markdown
# kitchen-docker-stack

A FastAPI kitchen (menu cached in Redis, orders in Postgres) packaged with a multi-stage Dockerfile and Docker Compose, with layered health checks and a regression suite that gates the deploy.

## Setup

1. Install Docker Desktop and make sure it is running.
2. In this folder run:

```bash
docker compose up --build
```

## API docs URL

- Interactive docs: <http://localhost:8000/docs>
- Menu: <http://localhost:8000/menu>
- Liveness: <http://localhost:8000/health> (is the process alive?)
- Deep health: <http://localhost:8000/health/deep> (can it reach Postgres and Redis?)

## Run the tests (against the real stack)

```bash
docker compose --profile test run --rm --build tests
```

## Deploy gate

```bash
bash scripts/deploy.sh
```

Runs the tests first. If any fail, it prints `BLOCKED` and nothing is deployed. The same check runs on GitHub in `.github/workflows/ci.yml`.

## Stop everything

```bash
docker compose --profile test down -v
```

## Without Docker (advanced)

Run your own Postgres and Redis, set `DATABASE_URL` and `REDIS_URL`, then `uvicorn app.main:app --port 8000`. For tests also set `API_URL=http://localhost:8000` and run `pytest`.

See `DOCUMENTATION.md` for the full write-up.
```

### Design decisions

| Decision | Reason |
|---|---|
| Three Dockerfile stages: builder, runtime, test | The deployed image has no build tools and no test tools. A smaller image has fewer things that can break or be attacked. |
| Install from prepared wheels with `--no-index` | The runtime stage cannot accidentally download something different from what was built. |
| Run as user `kitchen` (not root) | If someone breaks into the app, they are not root inside the container. |
| Pinned versions in `requirements.txt` | The same versions everywhere, so "it works on my machine" means something. |
| Dockerfile `HEALTHCHECK` uses `/health` | Layer 1: only "is the process alive". |
| Compose `healthcheck` for `api` uses `/health/deep` | Layer 2: other services (the tests) start only when the api can really work. |
| Python for the health check commands | The slim image has no `curl`, but Python is already there. |
| `depends_on` with `condition: service_healthy` | The api starts after Postgres and Redis answer, not just after their containers exist. |
| `init_db` retries for about 30 seconds | Even with the right start order, a database can be a moment slow. A retry is cheaper than a crash loop. |
| The `tests` service has `profiles: ["test"]` | `docker compose up` runs the app only. Tests run when asked. |
| Tests talk to the api over real HTTP (`API_URL`) | They prove the real stack works: the real image, real Postgres, real Redis, real network names. |
| Failure cases tested in-process with dead addresses | Stopping Postgres inside the real stack would break the other tests. Pointing the app at a dead address tests the same code path safely. |
| Short timeouts on database and Redis connections | A dead dependency gives a quick error, not a request that hangs for a minute. |
| The menu still works when Redis is down | The cache is a speed-up, not a requirement. The app falls back to the database (`X-Cache: MISS`). |
| Health answers show `down: <ErrorName>` only | Never the connection string, which contains the password. A test checks this. |
| `X-Cache: HIT` / `MISS` header | Makes the cache visible, so a test (and you) can prove it works. |
| Menu cache expires after 30 seconds (`ex=`) | A cache with no expiry shows old data forever. |
| The server uses its own prices | The order total is calculated from the database. A price sent by the client is ignored. |
| Validation with Pydantic (`qty` 1 to 10, at least one item) | Bad input gets a clear 422 before it touches the database. |
| Settings from environment variables, read at call time | Same code runs on a laptop, in compose and in CI. Tests can change them. |
| Dev password `kitchen` only in the compose file, with a warning comment | Fine for local development, never for production. |
| `deploy.sh` uses `trap` to clean up | The stack and the database volume are removed even when the tests fail. |

---

## 7. Tests (26 tests)

There are two kinds of tests. `test_stack.py` runs over real HTTP against the running api (inside compose, `API_URL` is `http://api:8000`). `test_health_layers.py` runs the app in-process and points it at dead addresses, to prove what the health layers say when a dependency is down. If `API_URL` is missing, the stack tests **fail with a clear message** instead of silently skipping, because a skipped test in CI looks like a pass.

| # | Test | What it proves |
|---|---|---|
| 1 | `/health` returns 200 `{status: ok}` | Layer 1 works |
| 2 | `/health/deep` shows database ok and redis ok | Layer 2 works |
| 3 | `/docs` is served | The API docs URL works |
| 4 | `/openapi.json` lists exactly the five endpoints | The documented API is the real API |
| 5 | Menu comes from the database with the right items and prices | Postgres read |
| 6 | First menu request is `MISS`, the second is `HIT` | Redis cache works |
| 7 | The menu is really saved in Redis, with an expiry of 30 seconds or less | Cache expires |
| 8 | Place an order and read it back: total 150 for 2 Idli + 1 Dosa | Postgres write and read |
| 9 | Two orders get different ids | Ids are unique |
| 10 | A price sent by the client is ignored | Server owns the prices |
| 11 | Unknown order is 404 with a message | Missing order |
| 12 | Unknown menu item is 422 and names the id | Bad id rejected |
| 13 to 19 | Bad orders are 422: no items, qty 0, qty 11, menu id 0, qty not a number, no menu id, empty body | Validation |
| 20 | Liveness stays 200 even when the database and Redis are unreachable | Layer 1 does not depend on layer 2 |
| 21 | Deep health is 200 when both are up | Layer 2 healthy |
| 22 | Deep health is 503 when Redis is down, database still `ok` | Layer 2 sees Redis |
| 23 | Deep health is 503 when the database is down, Redis still `ok` | Layer 2 sees Postgres |
| 24 | Both down: both reported | Complete report |
| 25 | The password in the connection string never appears in the answer | No secrets leaked |
| 26 | Menu still works (`MISS`) when Redis is down | Cache is optional |

### `tests/conftest.py`

```python
import os

import httpx
import pytest
import redis as redis_lib


def _need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set. Run the tests with: docker compose --profile test run --rm tests "
            "(see README for running them without Docker)."
        )
    return value


@pytest.fixture(scope="session")
def api():
    """A client for the REAL running api service (not a pretend one)."""
    with httpx.Client(base_url=_need("API_URL"), timeout=5) as client:
        yield client


@pytest.fixture()
def redis_client():
    r = redis_lib.Redis.from_url(_need("REDIS_URL"), decode_responses=True)
    yield r
    r.close()


@pytest.fixture()
def settings():
    from app.settings import get_settings

    return get_settings()
```

### `tests/test_stack.py` (against the real stack)

```python
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
```

### `tests/test_health_layers.py` (with a dependency down)

```python
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
```

---

## 8. Results

| Check | Command | Result |
|---|---|---|
| Tests | `pytest` against the app running with real PostgreSQL 16 and real Redis 7 | **26 passed** |
| Compose file | `docker compose config` | Valid |
| Break-it check 1 | `/health` also asked the database | **1 test failed** |
| Break-it check 2 | `/health/deep` always answered 200 | **2 tests failed** |
| Break-it check 3 | The menu was never saved in Redis | **2 tests failed** |
| Break-it check 4 | Order total ignored the quantity | **1 test failed** |
| Break-it check 5 | Health answer leaked the connection string | **1 test failed** |
| Break-it check 6 | Menu crashed when Redis was down | **1 test failed** |
| Break-it check 7 | Server trusted a price from the client | **2 tests failed** |
| Restored original | `pytest` | **26 passed** |
| Docker build and start | `docker compose --profile test run --rm --build tests` | The three images built and the api, Postgres and Redis containers reached **Healthy** on a machine with Docker. The workspace that wrote this has no Docker engine, so the full containerised test run and `deploy.sh` were not run here. The import error that first appeared in the test container was reproduced and fixed here (see Problems) |

---

## 9. Problems avoided

| Problem | Cause | Fix |
|---|---|---|
| `No module named 'app'` when the tests ran inside the container | With the `pytest` command, pytest adds only the `tests` folder to the import path, not the folder above it, so `from app.main import app` failed. It only showed up in the container because running tests from the project folder by hand adds that folder too | Added `pytest.ini` with `pythonpath = .`, and the test stage of the Dockerfile copies it. Reproduced the exact error (19 tests found, 1 error) with the plain `pytest` command in a folder laid out like the container, and after the fix all 26 are found |
| Restart loop when the database is down | One health check that also asks the database makes Docker restart a healthy app | Two layers: liveness checks nothing else, deep checks dependencies |
| Tests starting before the api is ready | `depends_on` alone waits only for the container to start | `condition: service_healthy` on a real health check |
| Crash when the database is a moment slow | The api tried to connect once at startup | `init_db` retries for about 30 seconds |
| Hanging requests when Redis dies | No timeout on the connection | 1 second timeouts, and the menu falls back to the database |
| Skipped tests looking like a pass in CI | A missing `API_URL` could skip the stack tests | The tests fail loudly with a message instead of skipping |
| Password visible in a health answer | Printing the error or the URL | Show only `down: <ErrorName>`, and a test for it |
| Pytest cache permission error in the container | The app folder belongs to root, the app runs as `kitchen` | `PYTEST_ADDOPTS="-p no:cacheprovider"` in the test stage |
| Old menu forever | A cache entry with no expiry | `ex=30` seconds, and a test that checks the expiry |

---

## 10. What was practiced

- Writing a multi-stage Dockerfile, and running the app as a normal user.
- Writing a compose file with health checks and `depends_on: service_healthy`.
- Layering health checks: liveness (alive) and readiness (can really work).
- Putting a cache (Redis) in front of a database (Postgres), and keeping the app working when the cache is down.
- Testing a real stack over real HTTP, and testing failure cases safely in-process.
- Gating a deploy on a test run, locally (`deploy.sh`) and on GitHub (`ci.yml`).
- Keeping secrets out of logs and health answers.
- Checking that tests are real by breaking the code on purpose and watching them fail.

---

## 11. Known limit

Two things to know. First, the Docker files were checked with `docker compose config`, and the images built and started healthy on a machine with Docker, but the workspace that wrote this had no Docker engine, so the containerised test run was not repeated here. The app and all 26 tests were run for real against Postgres 16 and Redis 7. Second, the task text mentions reusing your Day 15 and Day 19 tests in the regression suite. Those files were not available, so the suite covers the menu, orders and health checks written here. To add them, copy those test files into `tests/` and add anything they need to `requirements-dev.txt`; the `tests` service runs everything in that folder. The dev database password is for local use only.

---

## 12. How to run it

```bash
cd kitchen-docker-stack

# 1. Run the whole stack (needs Docker Desktop running)
docker compose up --build
#    API docs:   http://localhost:8000/docs
#    Menu:       http://localhost:8000/menu
#    Health:     http://localhost:8000/health   and   /health/deep

# 2. Run the regression tests against the real stack (in a second terminal)
docker compose --profile test run --rm --build tests

# 3. The deploy gate (tests first, deploy only if they pass)
bash scripts/deploy.sh

# Stop and remove everything, including the database volume
docker compose --profile test down -v
```

Try the layers yourself: with the stack running, run `docker compose stop redis`, then open `/health` (still 200) and `/health/deep` (503, `redis: down`), and `/menu` still answers. Run `docker compose start redis` and `/health/deep` goes back to 200.
