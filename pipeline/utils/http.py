"""HTTP client with identifying User-Agent and per-host rate limiting."""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock
from urllib.parse import urlparse

import httpx

USER_AGENT = "ai-daily-news/1.0 (+https://github.com/) - automated AI news aggregator"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
MIN_INTERVAL_SEC = 1.0

_last_hit: dict[str, float] = defaultdict(float)
_lock = Lock()


def _throttle(url: str) -> None:
    host = urlparse(url).netloc
    with _lock:
        elapsed = time.monotonic() - _last_hit[host]
        if elapsed < MIN_INTERVAL_SEC:
            time.sleep(MIN_INTERVAL_SEC - elapsed)
        _last_hit[host] = time.monotonic()


def get_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
    )


def fetch(url: str, client: httpx.Client | None = None) -> httpx.Response:
    """Throttled GET. Caller handles non-2xx."""
    _throttle(url)
    if client is None:
        with get_client() as c:
            return c.get(url)
    return client.get(url)
