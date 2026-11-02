import redis


def client(url: str) -> redis.Redis:
    # Short timeouts: a dead Redis must not make every request hang.
    return redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)
