import os
from functools import lru_cache

import redis
from loguru import logger


class RedisUnavailableError(RuntimeError):
    """Raised when a Redis client cannot be created."""


def _create_client() -> redis.Redis:
    host = os.getenv("REDIS_HOST", "localhost")
    port_raw = os.getenv("REDIS_PORT", "6379")
    password = os.getenv("REDIS_PASSWORD") or None

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RedisUnavailableError(
            f"Invalid REDIS_PORT value '{port_raw}'. Expected integer."
        ) from exc

    try:
        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            decode_responses=True,
        )
        client.ping()
    except redis.RedisError as exc:
        raise RedisUnavailableError(
            f"Unable to connect to Redis at {host}:{port}: {exc}"
        ) from exc

    logger.debug("Connected to Redis at %s:%s", host, port)
    return client


@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis:
    """Return a cached Redis client, raising if unavailable."""

    return _create_client()
