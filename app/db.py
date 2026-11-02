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
