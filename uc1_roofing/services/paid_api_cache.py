"""Small cache helpers for paid external API calls.

The goal is to deduplicate repeat requests during the quoting workflow without
introducing schema changes or storing provider data permanently.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.core.cache import cache


CACHE_VERSION = "paid-api-v1"
SHORT_TTL_SECONDS = 15 * 60
MEDIUM_TTL_SECONDS = 6 * 60 * 60
LONG_TTL_SECONDS = 24 * 60 * 60
NEGATIVE_TTL_SECONDS = 60 * 60


def rounded_point(lat: float, lng: float, precision: int = 5) -> dict[str, float]:
    return {"lat": round(float(lat), precision), "lng": round(float(lng), precision)}


def normalized_address(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())[:220]


def make_cache_key(namespace: str, payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{CACHE_VERSION}:{namespace}:{digest}"


def get_cached(namespace: str, payload: Any) -> Any:
    return cache.get(make_cache_key(namespace, payload))


def set_cached(namespace: str, payload: Any, value: Any, ttl_seconds: int) -> None:
    cache.set(make_cache_key(namespace, payload), value, ttl_seconds)


def clone_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value))
