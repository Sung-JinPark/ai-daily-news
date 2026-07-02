"""Atomic text-file writes (AUDIT-1 AUD-006).

Several pipeline outputs are read-modify-rewrite streams (continuity,
aggregate JSONL, latest.json) where a mid-write interruption loses the
entire accumulated file, not just the current day. Routing writes
through tmp + os.replace makes the swap atomic on both NTFS and POSIX:
readers (and a subsequent ``git add``) see either the old or the new
complete file, never a torn one.
"""
from __future__ import annotations

import os
from pathlib import Path


def write_text_atomic(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)
