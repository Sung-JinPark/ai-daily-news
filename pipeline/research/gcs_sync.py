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

Uploaded object layout mirrors the local tree:

    gs://<bucket>/<prefix>/manifest.json
    gs://<bucket>/<prefix>/snapshots/YYYY-MM-DD/*.parquet
    gs://<bucket>/<prefix>/timeseries/*.parquet

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

PRIVATE_ROOT = Path("data") / "research_private"
DEFAULT_PREFIX = "ai-daily-news/research_private"

# NOTE: data/papers_private/ (the arXiv paper corpus / SQLite DB) is
# also gitignored and privacy-sensitive, but it's a live SQLite file
# and needs snapshot-style export rather than a naive rsync (avoid
# uploading mid-write). Wire that here once the paper ingest pipeline
# ships a checkpoint export step. For now this uploader mirrors only
# data/research_private/.


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


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="log only, no uploads")
    args = parser.parse_args()
    sync(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
