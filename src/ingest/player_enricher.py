"""Player Profile Bio Cache Enricher for IFA Bronze Layer.

Checks player_ids from newly ingested matches against data/bronze/players/bronze_players.parquet,
identifies unseen players or players whose club/team_id changed, fetches profile metadata
via Playwright DOM extraction, and appends timestamped snapshots for SCD Type 2 tracking.
"""

import argparse
import asyncio
from datetime import date, datetime, timezone
import logging
import os
from pathlib import Path
import sys
from typing import Any, Optional

# Ensure repository root is on sys.path for direct CLI script execution
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.parsers.ifa_parser import parse_player_page
from src.schemas import (
    PLAYERS_SNAPSHOT_PYARROW_SCHEMA,
    PlayerAppearance,
    PlayerSnapshot,
)
from src.utils.run_logger import ETLRunLogger
from src.utils.s3_storage import sync_file_to_r2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("player_enricher")

IFA_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# =============================================================================
# Bio Cache Filter & PyArrow Storage Helpers
# =============================================================================

def get_existing_player_cache(bronze_players_path: Path | str) -> dict[int, dict[str, Any]]:
    """Query the latest snapshot per player_id from bronze_players.parquet.
    
    Returns:
        Mapping of player_id -> {"current_team_id": int | None, "birth_date": date | None, ...}
    """
    path = Path(bronze_players_path)
    if not path.exists():
        return {}

    con = duckdb.connect()
    try:
        query = f"""
            SELECT player_id, current_team_id, current_league_id, birth_date
            FROM (
                SELECT player_id, current_team_id, current_league_id, birth_date,
                       ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY scraped_at DESC) as rn
                FROM read_parquet('{path.as_posix()}')
            )
            WHERE rn = 1
        """
        rows = con.execute(query).fetchall()
    except Exception as exc:
        logger.warning(f"Failed to query existing player cache at {path}: {exc}")
        return {}
    finally:
        con.close()

    cache: dict[int, dict[str, Any]] = {}
    for p_id, c_team, c_league, b_date in rows:
        if p_id is None:
            continue
        p_id = int(p_id)
        cache[p_id] = {
            "current_team_id": int(c_team) if c_team is not None else None,
            "current_league_id": int(c_league) if c_league is not None else None,
            "birth_date": b_date.date() if hasattr(b_date, "date") else b_date,
        }
    return cache


def filter_players_needing_enrichment(
    candidates: list[dict[str, Any]],
    existing_cache: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter candidates down to unseen players or players whose team_id changed.
    
    Args:
        candidates: List of candidate dicts with keys: player_id, team_id, league_id, team_name.
        existing_cache: Output of get_existing_player_cache.
        
    Returns:
        Deduplicated list of candidate dicts requiring IFA profile scraping.
    """
    needed: list[dict[str, Any]] = []
    seen_in_batch: set[int] = set()

    for cand in candidates:
        p_id = cand["player_id"]
        if p_id in seen_in_batch:
            continue

        cand_team_id = cand.get("team_id")

        if p_id not in existing_cache:
            # Unseen player
            needed.append(cand)
            seen_in_batch.add(p_id)
        else:
            cached_team_id = existing_cache[p_id].get("current_team_id")
            # If candidate has team_id and it differs from cached team_id -> club transfer
            if cand_team_id is not None and cached_team_id is not None and cand_team_id != cached_team_id:
                needed.append(cand)
                seen_in_batch.add(p_id)

    return needed


def append_player_snapshots(
    bronze_players_path: Path | str,
    snapshots: list[PlayerSnapshot],
    sync_r2: bool = True,
) -> int:
    """Append validated PlayerSnapshot instances to bronze_players.parquet.
    
    Returns:
        Number of total snapshot rows in the Parquet file.
    """
    if not snapshots:
        return 0

    path = Path(bronze_players_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    records = []
    for s in snapshots:
        records.append({
            "scraped_at": s.scraped_at,
            "player_id": s.player_id,
            "birth_date": s.birth_date,
            "current_team_id": s.current_team_id,
            "current_league_id": s.current_league_id,
            "full_name": s.full_name,
            "nationality": s.nationality,
            "current_team_name": s.current_team_name,
            "current_league_name": s.current_league_name,
        })

    new_table = pa.Table.from_pylist(records, schema=PLAYERS_SNAPSHOT_PYARROW_SCHEMA)

    if path.exists():
        try:
            existing_table = pq.read_table(path)
            combined_table = pa.concat_tables([existing_table, new_table])
        except Exception as exc:
            logger.warning(f"Error reading existing {path}, overwriting: {exc}")
            combined_table = new_table
    else:
        combined_table = new_table

    pq.write_table(combined_table, path, compression="snappy")
    logger.info(f"Appended {len(snapshots)} player snapshots to {path} (total rows: {len(combined_table)})")

    if sync_r2:
        sync_file_to_r2(path, "players/bronze_players.parquet")

    return len(combined_table)


# =============================================================================
# Playwright Async Enricher Core
# =============================================================================

class PlayerEnricher:
    """Async Playwright scraper to enrich player profiles and bio cache."""

    def __init__(
        self,
        bronze_dir: Path | str = "data/bronze",
        headless: bool = True,
        sync_r2: bool = True,
    ):
        self.bronze_dir = Path(bronze_dir)
        self.players_path = self.bronze_dir / "players" / "bronze_players.parquet"
        self.headless = headless
        self.sync_r2 = sync_r2

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def fetch_player_profile(
        self,
        page,
        player_id: int,
        season_id: int = 28,
    ) -> str:
        """Fetch player profile HTML from football.org.il with tenacity retries."""
        url = f"https://www.football.org.il/players/player/?player_id={player_id}&season_id={season_id}"
        logger.debug(f"Navigating to {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        return await page.content()

    async def enrich_candidates(
        self,
        candidates: list[dict[str, Any]],
        season_id: int = 28,
        dry_run: bool = False,
        run_logger: Optional[ETLRunLogger] = None,
    ) -> list[PlayerSnapshot]:
        """Check candidates against existing bio cache and scrape required profiles."""
        existing_cache = get_existing_player_cache(self.players_path)
        logger.info(f"Existing player bio cache has {len(existing_cache)} distinct players")

        needed = filter_players_needing_enrichment(candidates, existing_cache)
        logger.info(f"{len(needed)} players need profile enrichment (unseen or changed clubs)")

        if dry_run and len(needed) > 5:
            needed = needed[:5]
            logger.info(f"Dry-run active: limited enrichment to {len(needed)} players")

        if not needed:
            logger.info("No players require enrichment.")
            return []

        from playwright.async_api import async_playwright

        snapshots: list[PlayerSnapshot] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent=IFA_USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            for cand in needed:
                p_id = cand["player_id"]
                try:
                    html = await self.fetch_player_profile(page, p_id, season_id=season_id)
                    profile = parse_player_page(html, player_id=p_id, season_id=season_id)

                    snapshot = PlayerSnapshot(
                        scraped_at=datetime.now(timezone.utc),
                        player_id=p_id,
                        birth_date=profile.birth_date,
                        current_team_id=profile.current_team_id or cand.get("team_id"),
                        current_league_id=profile.current_league_id or cand.get("league_id"),
                        full_name=profile.full_name or cand.get("player_name"),
                        nationality=profile.nationality,
                        current_team_name=profile.current_team_name or cand.get("team_name"),
                        current_league_name=profile.current_league_name,
                    )
                    snapshots.append(snapshot)
                    if run_logger:
                        run_logger.increment_players(1)
                except Exception as exc:
                    logger.error(f"Failed to enrich player {p_id}: {exc}")

            await browser.close()

        if snapshots:
            append_player_snapshots(
                bronze_players_path=self.players_path,
                snapshots=snapshots,
                sync_r2=self.sync_r2,
            )
            if run_logger:
                run_logger.increment_rows(len(snapshots))

        return snapshots

    def extract_candidates_from_bronze(self, season_id: Optional[int] = None) -> list[dict[str, Any]]:
        """Extract all candidate player appearances from existing Bronze Parquet files."""
        pattern = self.bronze_dir.as_posix() + "/**/appearances.parquet"
        con = duckdb.connect()
        try:
            query = f"""
                SELECT DISTINCT player_id, team_name, season_id, league_id
                FROM read_parquet('{pattern}', hive_partitioning=1)
            """
            if season_id is not None:
                query += f" WHERE season_id = {season_id}"
            rows = con.execute(query).fetchall()
        except Exception as exc:
            logger.warning(f"Could not load appearances from {pattern}: {exc}")
            return []
        finally:
            con.close()

        candidates: list[dict[str, Any]] = []
        for p_id, t_name, s_id, l_id in rows:
            if p_id is None:
                continue
            candidates.append({
                "player_id": int(p_id),
                "team_name": t_name,
                "team_id": None,  # Will be enriched from profile or match metadata
                "season_id": int(s_id) if s_id is not None else None,
                "league_id": int(l_id) if l_id is not None else None,
            })
        return candidates


def main():
    """CLI entrypoint for player_enricher."""
    parser = argparse.ArgumentParser(description="IFA Bronze Player Profile Bio Cache Enricher")
    parser.add_argument("--season-id", type=int, default=28, help="IFA season ID (default 28)")
    parser.add_argument("--bronze-dir", type=str, default="data/bronze", help="Bronze storage directory")
    parser.add_argument("--dry-run", action="store_true", help="Limit enrichment to 5 players for testing")
    parser.add_argument("--headless", type=str, default=os.getenv("HEADLESS_BROWSER", "true"), help="Headless browser execution (true/false)")
    parser.add_argument("--no-r2-sync", action="store_true", help="Disable Cloudflare R2 synchronization")

    args = parser.parse_args()
    headless_bool = str(args.headless).lower() in ("true", "1", "yes")

    enricher = PlayerEnricher(
        bronze_dir=args.bronze_dir,
        headless=headless_bool,
        sync_r2=not args.no_r2_sync,
    )

    candidates = enricher.extract_candidates_from_bronze(season_id=args.season_id)
    logger.info(f"Found {len(candidates)} candidate players from Bronze appearances")

    with ETLRunLogger(log_path=Path(args.bronze_dir) / "etl_run_logs.parquet", sync_r2=not args.no_r2_sync) as rlogger:
        snapshots = asyncio.run(
            enricher.enrich_candidates(
                candidates=candidates,
                season_id=args.season_id,
                dry_run=args.dry_run,
                run_logger=rlogger,
            )
        )
        logger.info(f"Enriched {len(snapshots)} player profiles.")


if __name__ == "__main__":
    main()
