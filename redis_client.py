import redis
import os
import secrets
import config

REDIS_ENABLED = getattr(config, "redis_enabled", False) or os.getenv("REDIS_ENABLED", "false").lower() == "true"

if not REDIS_ENABLED:
    raise RuntimeError("Redis is not enabled in the configuration.")

REDIS_HOST = getattr(config, "redis_host", os.getenv("REDIS_HOST", "localhost"))
REDIS_PORT = int(getattr(config, "redis_port", os.getenv("REDIS_PORT", 6379)))
REDIS_DB   = int(getattr(config, "redis_db", os.getenv("REDIS_DB", 0)))

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True  # strings, not bytes
)

def gen_token():
    return secrets.token_urlsafe(5)
