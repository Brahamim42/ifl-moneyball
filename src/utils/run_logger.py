"""Structured run metrics logger writing to data/bronze/etl_run_logs.parquet."""

from datetime import datetime, timezone
import logging
from pathlib import Path
import sys
from typing import Any, Optional
import uuid

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pyarrow as pa
import pyarrow.parquet as pq

from src.utils.s3_storage import sync_file_to_r2

logger = logging.getLogger(__name__)

RUN_LOG_SCHEMA = pa.schema([
    ("run_id", pa.string()),
    ("started_at", pa.timestamp("us", tz="UTC")),
    ("finished_at", pa.timestamp("us", tz="UTC")),
    ("matches_scraped", pa.int64()),
    ("players_enriched", pa.int64()),
    ("rows_written", pa.int64()),
    ("status", pa.string()),
    ("error_message", pa.string()),
])


class ETLRunLogger:
    """Context manager and tracker for ingestion run metrics."""

    def __init__(
        self,
        log_path: Path | str = "data/bronze/etl_run_logs.parquet",
        run_id: Optional[str] = None,
        sync_r2: bool = True,
    ):
        self.log_path = Path(log_path)
        self.run_id = run_id or uuid.uuid4().hex
        self.sync_r2 = sync_r2
        self.started_at: datetime = datetime.now(timezone.utc)
        self.finished_at: Optional[datetime] = None
        self.matches_scraped: int = 0
        self.players_enriched: int = 0
        self.rows_written: int = 0
        self.status: str = "RUNNING"
        self.error_message: Optional[str] = None
        self._is_finished: bool = False

    def increment_matches(self, count: int = 1) -> None:
        """Increment count of matches scraped."""
        self.matches_scraped += count

    def increment_players(self, count: int = 1) -> None:
        """Increment count of players enriched."""
        self.players_enriched += count

    def increment_rows(self, count: int = 1) -> None:
        """Increment count of Parquet rows written."""
        self.rows_written += count

    def finish(
        self,
        status: str = "SUCCESS",
        error_message: Optional[str] = None,
    ) -> None:
        """Mark run as complete and write metrics to Parquet."""
        if self._is_finished:
            return
        self.finished_at = datetime.now(timezone.utc)
        self.status = status
        self.error_message = error_message
        self._write_log()
        self._is_finished = True

    def _write_log(self) -> None:
        """Append log record to etl_run_logs.parquet."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        new_data: dict[str, list[Any]] = {
            "run_id": [self.run_id],
            "started_at": [self.started_at],
            "finished_at": [self.finished_at or datetime.now(timezone.utc)],
            "matches_scraped": [self.matches_scraped],
            "players_enriched": [self.players_enriched],
            "rows_written": [self.rows_written],
            "status": [self.status],
            "error_message": [self.error_message],
        }
        new_table = pa.Table.from_pydict(new_data, schema=RUN_LOG_SCHEMA)

        if self.log_path.exists():
            try:
                existing_table = pq.read_table(self.log_path)
                combined_table = pa.concat_tables([existing_table, new_table])
            except Exception as exc:
                logger.warning(f"Could not read existing run logs at {self.log_path}, overwriting: {exc}")
                combined_table = new_table
        else:
            combined_table = new_table

        pq.write_table(combined_table, self.log_path, compression="snappy")
        logger.info(f"ETL run logged: run_id={self.run_id} status={self.status} rows={self.rows_written} to {self.log_path}")

        if self.sync_r2:
            sync_file_to_r2(self.log_path, "etl_run_logs.parquet")

    def __enter__(self) -> "ETLRunLogger":
        self.started_at = datetime.now(timezone.utc)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.finish(status="FAILED", error_message=str(exc_val))
        else:
            if not self._is_finished:
                self.finish(status="SUCCESS")

    @classmethod
    def read_logs(cls, log_path: Path | str = "data/bronze/etl_run_logs.parquet") -> pa.Table:
        """Read run logs Parquet table."""
        path = Path(log_path)
        if not path.exists():
            return pa.Table.from_arrays([pa.array([], type=f.type) for f in RUN_LOG_SCHEMA], schema=RUN_LOG_SCHEMA)
        return pq.read_table(path)
