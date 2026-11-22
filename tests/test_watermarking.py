"""Unit tests for Bronze incremental watermarking and idempotent Parquet writing."""

from datetime import date, timedelta
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.ingest.ifa_crawler import (
    get_existing_match_watermark,
    write_matches_and_appearances,
)
from src.ingest.player_enricher import (
    append_player_snapshots,
    filter_players_needing_enrichment,
    get_existing_player_cache,
)
from src.schemas import MatchMetadata, PlayerAppearance, PlayerSnapshot


@pytest.fixture
def temp_bronze_dir(tmp_path: Path) -> Path:
    """Fixture providing an isolated temporary bronze directory."""
    bronze_dir = tmp_path / "data" / "bronze"
    bronze_dir.mkdir(parents=True, exist_ok=True)
    return bronze_dir


class TestMatchWatermarking:
    """Tests for incremental match watermarking in ifa_crawler."""

    def test_watermark_empty_bronze_returns_empty(self, temp_bronze_dir: Path):
        """When no matches exist in Bronze, watermark must be empty (all matches scraped)."""
        skip_ids = get_existing_match_watermark(
            bronze_dir=temp_bronze_dir,
            season_id=28,
            league_id=61,
            lookback_days=None,
        )
        assert skip_ids == set()

    def test_watermark_skips_all_existing_without_lookback(self, temp_bronze_dir: Path):
        """Without lookback_days, all matches present in Bronze must be skipped."""
        matches = [
            MatchMetadata(
                match_id=101,
                season_id=28,
                league_id=61,
                match_date=date(2026, 9, 1),
                home_team_name="קבוצה א",
                away_team_name="קבוצה ב",
            ),
            MatchMetadata(
                match_id=102,
                season_id=28,
                league_id=61,
                match_date=date(2026, 9, 8),
                home_team_name="קבוצה ג",
                away_team_name="קבוצה ד",
            ),
        ]
        write_matches_and_appearances(
            bronze_dir=temp_bronze_dir,
            season_id=28,
            league_id=61,
            matches=matches,
            appearances=[],
            sync_r2=False,
        )

        skip_ids = get_existing_match_watermark(
            bronze_dir=temp_bronze_dir,
            season_id=28,
            league_id=61,
            lookback_days=None,
        )
        assert skip_ids == {101, 102}

    def test_watermark_with_lookback_allows_recent_matches(self, temp_bronze_dir: Path):
        """With lookback_days=7, matches within the last 7 days must NOT be skipped."""
        today = date.today()
        recent_date = today - timedelta(days=2)
        old_date = today - timedelta(days=20)

        matches = [
            MatchMetadata(
                match_id=201,  # Old match (> 7 days) -> should be skipped
                season_id=28,
                league_id=61,
                match_date=old_date,
                home_team_name="קבוצה א",
                away_team_name="קבוצה ב",
            ),
            MatchMetadata(
                match_id=202,  # Recent match (within 7 days) -> must NOT be skipped (eligible for re-fetch)
                season_id=28,
                league_id=61,
                match_date=recent_date,
                home_team_name="קבוצה ג",
                away_team_name="קבוצה ד",
            ),
        ]
        write_matches_and_appearances(
            bronze_dir=temp_bronze_dir,
            season_id=28,
            league_id=61,
            matches=matches,
            appearances=[],
            sync_r2=False,
        )

        skip_ids = get_existing_match_watermark(
            bronze_dir=temp_bronze_dir,
            season_id=28,
            league_id=61,
            lookback_days=7,
        )

        # 201 is older than 7 days -> skip
        assert 201 in skip_ids
        # 202 is within 7 days -> do not skip (re-fetch for late corrections)
        assert 202 not in skip_ids

    def test_idempotent_write_does_not_duplicate_rows(self, temp_bronze_dir: Path):
        """Re-ingesting an updated match must replace the existing row, not duplicate it."""
        match_v1 = MatchMetadata(
            match_id=301,
            season_id=28,
            league_id=61,
            match_date=date(2026, 9, 1),
            home_team_name="קבוצה א",
            away_team_name="קבוצה ב",
            home_score=1,
            away_score=0,
        )
        app_v1 = PlayerAppearance(
            match_id=301,
            player_id=501,
            team_side="home",
            team_name="קבוצה א",
            player_name="שחקן א",
            is_starter=True,
            season_id=28,
            league_id=61,
        )

        # Initial write
        write_matches_and_appearances(
            bronze_dir=temp_bronze_dir,
            season_id=28,
            league_id=61,
            matches=[match_v1],
            appearances=[app_v1],
            sync_r2=False,
        )

        # Second write (stat correction: home_score changed to 2)
        match_v2 = MatchMetadata(
            match_id=301,
            season_id=28,
            league_id=61,
            match_date=date(2026, 9, 1),
            home_team_name="קבוצה א",
            away_team_name="קבוצה ב",
            home_score=2,  # corrected score
            away_score=0,
        )
        app_v2 = PlayerAppearance(
            match_id=301,
            player_id=501,
            team_side="home",
            team_name="קבוצה א",
            player_name="שחקן א",
            is_starter=True,
            goals_scored=1,  # added goal
            season_id=28,
            league_id=61,
        )

        write_matches_and_appearances(
            bronze_dir=temp_bronze_dir,
            season_id=28,
            league_id=61,
            matches=[match_v2],
            appearances=[app_v2],
            sync_r2=False,
        )

        matches_file = temp_bronze_dir / "season_id=28" / "league_id=61" / "matches.parquet"
        apps_file = temp_bronze_dir / "season_id=28" / "league_id=61" / "appearances.parquet"

        m_table = pq.read_table(matches_file)
        a_table = pq.read_table(apps_file)

        # Ensure no duplicated rows
        assert len(m_table) == 1
        assert len(a_table) == 1
        # Ensure updated values are present
        assert m_table["home_score"][0].as_py() == 2
        assert a_table["goals_scored"][0].as_py() == 1


class TestPlayerBioCacheFilter:
    """Tests for player profile enrichment filtering and bio cache."""

    def test_filter_unseen_players(self):
        """Unseen player_ids must always be flagged for profile scraping."""
        existing_cache = {
            1001: {"current_team_id": 50, "birth_date": date(2003, 1, 1)},
        }
        candidates = [
            {"player_id": 1001, "team_id": 50, "league_id": 61},  # Seen, same team
            {"player_id": 2002, "team_id": 60, "league_id": 61},  # Unseen
        ]

        needed = filter_players_needing_enrichment(candidates, existing_cache)
        assert len(needed) == 1
        assert needed[0]["player_id"] == 2002

    def test_filter_club_transfer_triggers_enrichment(self):
        """When an existing player appears with a different team_id, trigger enrichment."""
        existing_cache = {
            1001: {"current_team_id": 50, "birth_date": date(2003, 1, 1)},
        }
        candidates = [
            {"player_id": 1001, "team_id": 75, "league_id": 61},  # Changed from team 50 to 75
        ]

        needed = filter_players_needing_enrichment(candidates, existing_cache)
        assert len(needed) == 1
        assert needed[0]["player_id"] == 1001
        assert needed[0]["team_id"] == 75

    def test_filter_same_club_is_skipped(self):
        """When an existing player appears with the same team_id, skip scraping."""
        existing_cache = {
            1001: {"current_team_id": 50, "birth_date": date(2003, 1, 1)},
        }
        candidates = [
            {"player_id": 1001, "team_id": 50, "league_id": 61},  # Same team
        ]

        needed = filter_players_needing_enrichment(candidates, existing_cache)
        assert len(needed) == 0

    def test_append_snapshots_persists_history(self, temp_bronze_dir: Path):
        """Snapshots must append to bronze_players.parquet maintaining historical records."""
        from datetime import datetime, timezone

        players_path = temp_bronze_dir / "players" / "bronze_players.parquet"

        # Snapshot 1
        s1 = PlayerSnapshot(
            scraped_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            player_id=1001,
            birth_date=date(2003, 5, 12),
            current_team_id=50,
            current_league_id=61,
            full_name="דניאל כהן",
        )
        append_player_snapshots(players_path, [s1], sync_r2=False)

        cache1 = get_existing_player_cache(players_path)
        assert 1001 in cache1
        assert cache1[1001]["current_team_id"] == 50

        # Snapshot 2 (transfer to team 75)
        s2 = PlayerSnapshot(
            scraped_at=datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc),
            player_id=1001,
            birth_date=date(2003, 5, 12),
            current_team_id=75,
            current_league_id=61,
            full_name="דניאל כהן",
        )
        append_player_snapshots(players_path, [s2], sync_r2=False)

        # Cache must reflect latest snapshot
        cache2 = get_existing_player_cache(players_path)
        assert cache2[1001]["current_team_id"] == 75

        # Parquet file must contain both snapshots (for SCD Type 2 modeling)
        table = pq.read_table(players_path)
        assert len(table) == 2
