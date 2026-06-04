"""Fetch article URL, extract body and OG image. Body never stored on disk;
only the image URL (3rd-party hot-link) is persisted in articles.json."""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import trafilatura

from pipeline.utils.http import fetch, get_client

log = logging.getLogger(__name__)
MAX_CHARS = 16000

_OG_PATTERNS = [
    re.compile(
        r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']+)["\']' % re.escape(p),
        re.I | re.S,
    )
    for p in ("og:image", "twitter:image", "twitter:image:src")
] + [
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(p),
        re.I | re.S,
    )
    for p in ("og:image", "twitter:image", "twitter:image:src")
]


def _extract_og_image(html: str, page_url: str) -> str:
    for pat in _OG_PATTERNS:
        m = pat.search(html)
        if m:
            raw = m.group(1).strip()
            if raw.startswith("//"):
                return "https:" + raw
            if raw.startswith("/"):
                return urljoin(page_url, raw)
            if raw.startswith("http"):
                return raw
    return ""


def extract_article(url: str) -> dict[str, str]:
    """Returns {body, image_url}. Both can be empty strings."""
    try:
        with get_client() as client:
            resp = fetch(url, client=client)
            if resp.status_code >= 400:
                return {"body": "", "image_url": ""}
            html = resp.text
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch failed for %s: %s", url, exc)
        return {"body": "", "image_url": ""}

    body = ""
    try:
        text = trafilatura.extract(
            html, include_comments=False, include_tables=False, favor_precision=True
        )
        if text:
            body = text[:MAX_CHARS]
    except Exception as exc:  # noqa: BLE001
        log.warning("trafilatura failed for %s: %s", url, exc)

    image = _extract_og_image(html, url)
    return {"body": body, "image_url": image}


def extract_body(url: str) -> str:
    """Back-compat shim used by older callers / tests."""
    return extract_article(url)["body"]
