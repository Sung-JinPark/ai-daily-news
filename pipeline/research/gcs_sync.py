"""Optional Google Cloud Storage backup for the private research corpus.

This module is a skeleton -it activates only when the user has
provided GCP credentials via env vars. Without credentials it logs a
notice and returns cleanly so the snapshot pipeline never fails on
machines that opted out of cloud backup.

Env vars (all required to enable upload):

- ``GCS_BUCKET``                    -target bucket (e.g. ``my-ai-research``)
- ``GOOGLE_APPLICATION_CREDENTIALS`` -path to a service account JSON key
- ``GCS_PREFIX`` (optional)         -subpath inside the bucket
                                       (defaults to ``ai-daily-news/research_private``)

Uploaded object layout mirrors the local tree (rglob — everything
under research_private/ syncs), currently:

    gs://<bucket>/<prefix>/manifest.json
    gs://<bucket>/<prefix>/snapshots/YYYY-MM-DD/*
    gs://<bucket>/<prefix>/timeseries/*.parquet
    gs://<bucket>/<prefix>/paper_trends/*  briefs/*  exports/*
    gs://<bucket>/<prefix>/db_exports/papers-*.db research-*.db

To enable, install google-cloud-storage (deliberately kept optional so
the base install doesn't pull it):

    pip install google-cloud-storage

Then run:

    python -m pipeline.research.gcs_sync

The uploader is idempotent -it skips objects whose sha256 already
matches the manifest record. Local artifacts remain the source of truth.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

# .env is where GCS_BUCKET / GOOGLE_APPLICATION_CREDENTIALS live (see
# .env.example). Loading here means the scheduled batch run picks the
# credentials up without needing shell-level env configuration.
load_dotenv()

PRIVATE_ROOT = Path("data") / "research_private"
DEFAULT_PREFIX = "ai-daily-news/research_private"

# papers.db / research.db backup: handled — export_papers_db.py and
# export_dataset.py drop consistent cold checkpoints (sqlite3 backup
# API) into research_private/db_exports/, which this uploader already
# mirrors. The live DB files under papers_private/ are deliberately
# NOT synced (hot-copy torn-snapshot risk); the checkpoints are the
# backup artifact. (AUD-018: NOTE refreshed after C4-2/RDB-5 shipped.)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_client():
    """Return an authenticated GCS client, or ``None`` if unavailable."""
    try:
        from google.cloud import storage  # type: ignore
    except ImportError:
        print(
            "[gcs] google-cloud-storage not installed - skipping upload. "
            "Install with: pip install google-cloud-storage"
        )
        return None
    bucket_name = os.environ.get("GCS_BUCKET")
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not bucket_name:
        print("[gcs] GCS_BUCKET not set - skipping upload.")
        return None
    if not creds_path or not Path(creds_path).exists():
        print(
            "[gcs] GOOGLE_APPLICATION_CREDENTIALS missing or invalid - "
            "skipping upload."
        )
        return None
    client = storage.Client()
    return client.bucket(bucket_name)


def _iter_files(root: Path):
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def sync(dry_run: bool = False) -> dict:
    """Upload every file under ``data/research_private/`` to GCS.

    Returns a stats dict: {uploaded, skipped, missing_credentials}.
    """
    if not PRIVATE_ROOT.exists():
        print(f"[gcs] {PRIVATE_ROOT} does not exist - nothing to upload.")
        return {"uploaded": 0, "skipped": 0, "missing_credentials": False}

    prefix = os.environ.get("GCS_PREFIX", DEFAULT_PREFIX).strip("/")
    bucket = _load_client()
    if bucket is None:
        return {"uploaded": 0, "skipped": 0, "missing_credentials": True}

    uploaded = 0
    skipped = 0
    for local in _iter_files(PRIVATE_ROOT):
        rel = local.relative_to(PRIVATE_ROOT).as_posix()
        remote_path = f"{prefix}/{rel}"
        blob = bucket.blob(remote_path)
        local_sha = _sha256_file(local)
        # Reuse the local sha stored in blob metadata to avoid re-uploading.
        if blob.exists():
            blob.reload()
            existing_sha = (blob.metadata or {}).get("sha256")
            if existing_sha == local_sha:
                skipped += 1
                continue
        if dry_run:
            print(f"[gcs] DRY-RUN would upload {rel}")
            uploaded += 1
            continue
        blob.metadata = {"sha256": local_sha}
        blob.upload_from_filename(str(local))
        uploaded += 1
        print(f"[gcs] uploaded {rel}")

    print(f"[gcs] done - uploaded={uploaded} skipped={skipped}")
    return {"uploaded": uploaded, "skipped": skipped, "missing_credentials": False}


def self_check() -> dict:
    """Credential-free audit of what an actual sync would touch.

    Walks ``data/research_private/`` locally, enumerates every file
    under it (including any historical backlog — the walker uses
    ``rglob('*')`` so nothing is skipped), and prints a summary. This
    lets the researcher confirm the backup would be complete before
    handing GCP credentials over. It never contacts the network and
    never requires ``google-cloud-storage`` to be installed.
    """
    prefix = os.environ.get("GCS_PREFIX", DEFAULT_PREFIX).strip("/")
    print(f"[gcs] self-check - target prefix: gs://<bucket>/{prefix}/")
    if not PRIVATE_ROOT.exists():
        print(f"[gcs] {PRIVATE_ROOT} does not exist - nothing would upload.")
        return {"files": 0, "bytes": 0, "root_exists": False}
    files = list(_iter_files(PRIVATE_ROOT))
    total_bytes = sum(p.stat().st_size for p in files)
    by_subdir: dict[str, dict[str, int]] = {}
    for p in files:
        rel = p.relative_to(PRIVATE_ROOT)
        top = rel.parts[0] if len(rel.parts) > 1 else "."
        b = by_subdir.setdefault(top, {"files": 0, "bytes": 0})
        b["files"] += 1
        b["bytes"] += p.stat().st_size
    print(f"[gcs] would upload {len(files)} files ({total_bytes:,} bytes) from {PRIVATE_ROOT}")
    for top in sorted(by_subdir):
        s = by_subdir[top]
        print(f"[gcs]   {top:<20} {s['files']:>5} files  {s['bytes']:>12,} bytes")
    creds_ready = bool(os.environ.get("GCS_BUCKET")) and bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    print(f"[gcs] credentials configured: {creds_ready}")
    return {
        "files": len(files),
        "bytes": total_bytes,
        "root_exists": True,
        "by_subdir": by_subdir,
        "credentials_configured": creds_ready,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="log only, no uploads (requires credentials)")
    parser.add_argument("--self-check", action="store_true", help="offline audit - no credentials needed")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    else:
        sync(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
