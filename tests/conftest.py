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
