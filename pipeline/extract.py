"""Fetch article URL and extract main body text in memory only. Never stored."""
from __future__ import annotations

import logging

import trafilatura

from pipeline.utils.http import fetch, get_client

log = logging.getLogger(__name__)
MAX_CHARS = 16000  # ~4000 tokens cap before tiktoken truncation


def extract_body(url: str) -> str:
    """Returns extracted main text or empty string. Never persists to disk."""
    try:
        with get_client() as client:
            resp = fetch(url, client=client)
            if resp.status_code >= 400:
                return ""
            html = resp.text
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch failed for %s: %s", url, exc)
        return ""
    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("trafilatura failed for %s: %s", url, exc)
        return ""
    if not text:
        return ""
    return text[:MAX_CHARS]
