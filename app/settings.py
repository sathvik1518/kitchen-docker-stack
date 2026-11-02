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
