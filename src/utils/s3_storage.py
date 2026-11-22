"""Cloudflare R2 / S3 storage utilities using PyArrow S3FileSystem."""

import logging
import os
from pathlib import Path
from typing import Optional

import pyarrow.fs as pafs

logger = logging.getLogger(__name__)


def get_s3_filesystem() -> Optional[pafs.S3FileSystem]:
    """Initialize PyArrow S3FileSystem if Cloudflare R2 / S3 env vars are set."""
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    bucket = os.getenv("R2_BUCKET_NAME")
    endpoint = os.getenv("R2_ENDPOINT_URL")
    account_id = os.getenv("CLOUDFLARE_R2_ACCOUNT_ID")

    if not (access_key and secret_key and bucket):
        return None

    if not endpoint and account_id:
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    try:
        kwargs: dict = {
            "access_key": access_key,
            "secret_key": secret_key,
            "region": "auto",
        }
        if endpoint:
            kwargs["endpoint_override"] = endpoint

        return pafs.S3FileSystem(**kwargs)
    except Exception as exc:
        logger.warning(f"Failed to initialize S3/R2 filesystem: {exc}")
        return None


def sync_file_to_r2(local_path: Path | str, relative_key: str) -> bool:
    """Sync a local Parquet file to Cloudflare R2 if configured.
    
    Args:
        local_path: Local filesystem Path to the source file.
        relative_key: S3 object key within the bucket (e.g. 'season_id=28/league_id=61/matches.parquet').
        
    Returns:
        True if synced to R2, False if R2 not configured or sync failed.
    """
    s3_fs = get_s3_filesystem()
    if s3_fs is None:
        return False

    bucket = os.getenv("R2_BUCKET_NAME")
    if not bucket:
        return False

    local_path = Path(local_path)
    if not local_path.exists():
        logger.warning(f"Local file does not exist for R2 sync: {local_path}")
        return False

    dest_key = f"{bucket}/{relative_key.lstrip('/')}"
    try:
        with open(local_path, "rb") as src:
            with s3_fs.open_output_stream(dest_key) as dst:
                dst.write(src.read())
        logger.info(f"Synced {local_path} -> s3://{dest_key}")
        return True
    except Exception as exc:
        logger.error(f"Error syncing {local_path} to R2: {exc}")
        return False
