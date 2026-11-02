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
