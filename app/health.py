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
