"""Unit tests for ETL run metrics logging to Parquet."""

from pathlib import Path
import pyarrow.parquet as pq
import pytest

from src.utils.run_logger import ETLRunLogger, RUN_LOG_SCHEMA


@pytest.fixture
def temp_log_path(tmp_path: Path) -> Path:
    """Fixture providing temporary path for run logs."""
    return tmp_path / "data" / "bronze" / "etl_run_logs.parquet"


class TestETLRunLogger:
    """Tests for structured ETL run logger."""

    def test_logger_successful_run(self, temp_log_path: Path):
        """Verify successful context manager execution logs SUCCESS with metrics."""
        with ETLRunLogger(log_path=temp_log_path, sync_r2=False) as logger:
            logger.increment_matches(15)
            logger.increment_players(42)
            logger.increment_rows(500)
            run_id = logger.run_id

        assert temp_log_path.exists()
        table = ETLRunLogger.read_logs(temp_log_path)
        assert len(table) == 1

        row = table.to_pylist()[0]
        assert row["run_id"] == run_id
        assert row["status"] == "SUCCESS"
        assert row["error_message"] is None
        assert row["matches_scraped"] == 15
        assert row["players_enriched"] == 42
        assert row["rows_written"] == 500
        assert row["started_at"] is not None
        assert row["finished_at"] is not None
        assert row["finished_at"] >= row["started_at"]

    def test_logger_failure_run_captures_error(self, temp_log_path: Path):
        """Verify exception during run is caught, logged as FAILED with error message, and re-raised."""
        with pytest.raises(ValueError, match="Connection reset by peer"):
            with ETLRunLogger(log_path=temp_log_path, sync_r2=False) as logger:
                logger.increment_matches(3)
                raise ValueError("Connection reset by peer")

        assert temp_log_path.exists()
        table = ETLRunLogger.read_logs(temp_log_path)
        assert len(table) == 1

        row = table.to_pylist()[0]
        assert row["status"] == "FAILED"
        assert "Connection reset by peer" in row["error_message"]
        assert row["matches_scraped"] == 3

    def test_logger_multiple_runs_append(self, temp_log_path: Path):
        """Multiple ingestion runs must append sequential log rows."""
        # Run 1
        with ETLRunLogger(log_path=temp_log_path, sync_r2=False) as logger1:
            logger1.increment_matches(10)

        # Run 2
        with ETLRunLogger(log_path=temp_log_path, sync_r2=False) as logger2:
            logger2.increment_matches(20)

        table = ETLRunLogger.read_logs(temp_log_path)
        assert len(table) == 2

        rows = table.to_pylist()
        assert rows[0]["matches_scraped"] == 10
        assert rows[1]["matches_scraped"] == 20
        assert rows[0]["run_id"] != rows[1]["run_id"]

    def test_read_empty_log_returns_empty_table(self, tmp_path: Path):
        """Reading from a non-existent log path returns an empty PyArrow Table with correct schema."""
        missing_path = tmp_path / "non_existent.parquet"
        table = ETLRunLogger.read_logs(missing_path)
        assert len(table) == 0
        assert table.schema == RUN_LOG_SCHEMA

    def test_manual_finish_without_context_manager(self, temp_log_path: Path):
        """Verify manual start and finish lifecycle works as expected."""
        logger = ETLRunLogger(log_path=temp_log_path, sync_r2=False)
        logger.increment_matches(5)
        logger.increment_players(10)
        logger.finish(status="SUCCESS")

        table = ETLRunLogger.read_logs(temp_log_path)
        assert len(table) == 1
        assert table["matches_scraped"][0].as_py() == 5
        assert table["players_enriched"][0].as_py() == 10
