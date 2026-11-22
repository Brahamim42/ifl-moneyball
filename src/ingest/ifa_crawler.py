"""Production Bronze Ingestion Crawler for IFA (football.org.il) matches and appearances.

Uses asyncio, Playwright, tenacity retry, selectolax, pyarrow, and duckdb to crawl
completed league matches, apply incremental watermarking, parse DOM structures,
and persist Hive-partitioned Parquet datasets.
"""

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
import html as html_lib
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Optional
import xml.etree.ElementTree as ET

# Ensure repository root is on sys.path for direct CLI script execution
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from selectolax.lexbor import LexborHTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
import yaml

from src.parsers.ifa_parser import parse_game_page
from src.schemas import (
    APPEARANCES_PYARROW_SCHEMA,
    MATCHES_PYARROW_SCHEMA,
    LeagueSeason,
    MatchMetadata,
    PlayerAppearance,
    parse_hebrew_date,
)
from src.utils.run_logger import ETLRunLogger
from src.utils.s3_storage import sync_file_to_r2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ifa_crawler")

IFA_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# =============================================================================
# Watermarking & Storage Helpers
# =============================================================================

def get_existing_match_watermark(
    bronze_dir: Path | str,
    season_id: int,
    league_id: int,
    lookback_days: Optional[int] = None,
) -> set[int]:
    """Determine match_ids to skip based on existing Bronze Parquet files.
    
    Args:
        bronze_dir: Root Bronze storage directory.
        season_id: Target IFA season ID.
        league_id: Target IFA league ID.
        lookback_days: If specified (N > 0), only matches older than N days are skipped,
                       allowing recent matches to be re-fetched for late stat corrections.
                       If None or <= 0, all existing match_ids are skipped.
                       
    Returns:
        Set of match_ids that should be skipped.
    """
    matches_path = Path(bronze_dir) / f"season_id={season_id}" / f"league_id={league_id}" / "matches.parquet"
    if not matches_path.exists():
        return set()

    con = duckdb.connect()
    try:
        # Read match_id and match_date from existing Parquet using pure fetchall
        rows = con.execute(
            f"SELECT match_id, match_date FROM read_parquet('{matches_path.as_posix()}')"
        ).fetchall()
    except Exception as exc:
        logger.warning(f"Could not read watermark from {matches_path}: {exc}")
        return set()
    finally:
        con.close()

    if not rows:
        return set()

    if lookback_days is None or lookback_days <= 0:
        return {int(r[0]) for r in rows if r[0] is not None}

    cutoff = date.today() - timedelta(days=lookback_days)
    skip_ids: set[int] = set()

    for m_id, m_date in rows:
        if m_id is None:
            continue
        m_id = int(m_id)
        if m_date is not None:
            if hasattr(m_date, "date"):
                d = m_date.date()
            elif isinstance(m_date, date):
                d = m_date
            else:
                d = None

            if d is not None and d < cutoff:
                skip_ids.add(m_id)
        # If m_date is None, don't skip so it can be re-fetched and updated

    return skip_ids


def matches_to_pyarrow_table(matches: list[MatchMetadata]) -> pa.Table:
    """Convert MatchMetadata instances to validated PyArrow Table."""
    records = []
    for m in matches:
        records.append({
            "match_id": m.match_id,
            "season_id": m.season_id,
            "league_id": m.league_id,
            "round_number": m.round_number,
            "match_date": m.match_date,
            "match_time": m.match_time,
            "home_team_id": m.home_team_id,
            "home_team_name": m.home_team_name,
            "away_team_id": m.away_team_id,
            "away_team_name": m.away_team_name,
            "home_score": m.home_score,
            "away_score": m.away_score,
            "home_half_time_score": m.home_half_time_score,
            "away_half_time_score": m.away_half_time_score,
            "stadium": m.stadium,
            "referee": m.referee,
        })
    return pa.Table.from_pylist(records, schema=MATCHES_PYARROW_SCHEMA)


def appearances_to_pyarrow_table(appearances: list[PlayerAppearance]) -> pa.Table:
    """Convert PlayerAppearance instances to validated PyArrow Table."""
    records = []
    for a in appearances:
        records.append({
            "match_id": a.match_id,
            "player_id": a.player_id,
            "team_side": a.team_side,
            "team_name": a.team_name,
            "player_name": a.player_name,
            "shirt_number": a.shirt_number,
            "is_starter": a.is_starter,
            "is_captain": a.is_captain,
            "is_goalkeeper": a.is_goalkeeper,
            "sub_in_minute": a.sub_in_minute,
            "sub_out_minute": a.sub_out_minute,
            "yellow_card_minute": a.yellow_card_minute,
            "red_card_minute": a.red_card_minute,
            "goals_scored": a.goals_scored,
            "goal_minutes": a.goal_minutes,
            "minutes_played": a.minutes_played,
            "season_id": a.season_id,
            "league_id": a.league_id,
        })
    return pa.Table.from_pylist(records, schema=APPEARANCES_PYARROW_SCHEMA)


def write_matches_and_appearances(
    bronze_dir: Path | str,
    season_id: int,
    league_id: int,
    matches: list[MatchMetadata],
    appearances: list[PlayerAppearance],
    sync_r2: bool = True,
) -> tuple[int, int]:
    """Idempotently write matches and appearances into Hive-partitioned Bronze Parquet.
    
    Partition layout:
      data/bronze/season_id={season_id}/league_id={league_id}/matches.parquet
      data/bronze/season_id={season_id}/league_id={league_id}/appearances.parquet
      
    Returns:
        (matches_count, appearances_count)
    """
    if not matches and not appearances:
        return 0, 0

    partition_dir = Path(bronze_dir) / f"season_id={season_id}" / f"league_id={league_id}"
    partition_dir.mkdir(parents=True, exist_ok=True)

    matches_file = partition_dir / "matches.parquet"
    appearances_file = partition_dir / "appearances.parquet"

    # 1. Matches Idempotent Write
    new_match_ids = {m.match_id for m in matches}
    new_matches_table = matches_to_pyarrow_table(matches)

    if matches_file.exists():
        try:
            existing_matches = pq.read_table(matches_file)
            # Remove any existing rows matching the newly fetched match_ids
            mask = [mid.as_py() not in new_match_ids for mid in existing_matches.column("match_id")]
            filtered_existing = existing_matches.filter(pa.array(mask))
            combined_matches = pa.concat_tables([filtered_existing, new_matches_table])
        except Exception as exc:
            logger.warning(f"Error reading existing {matches_file}, rewriting: {exc}")
            combined_matches = new_matches_table
    else:
        combined_matches = new_matches_table

    pq.write_table(combined_matches, matches_file, compression="snappy")
    if sync_r2:
        sync_file_to_r2(matches_file, f"season_id={season_id}/league_id={league_id}/matches.parquet")

    # 2. Appearances Idempotent Write
    new_apps_table = appearances_to_pyarrow_table(appearances)

    if appearances_file.exists():
        try:
            existing_apps = pq.read_table(appearances_file)
            # Remove any existing appearance rows matching the newly fetched match_ids
            mask = [mid.as_py() not in new_match_ids for mid in existing_apps.column("match_id")]
            filtered_existing_apps = existing_apps.filter(pa.array(mask))
            combined_apps = pa.concat_tables([filtered_existing_apps, new_apps_table])
        except Exception as exc:
            logger.warning(f"Error reading existing {appearances_file}, rewriting: {exc}")
            combined_apps = new_apps_table
    else:
        combined_apps = new_apps_table

    pq.write_table(combined_apps, appearances_file, compression="snappy")
    if sync_r2:
        sync_file_to_r2(appearances_file, f"season_id={season_id}/league_id={league_id}/appearances.parquet")

    return len(combined_matches), len(combined_apps)


# =============================================================================
# Playwright Async Crawler Core
# =============================================================================

class IFACrawler:
    """Production async Playwright crawler for IFA target leagues."""

    def __init__(
        self,
        config_path: Path | str = "config/leagues.yaml",
        bronze_dir: Path | str = "data/bronze",
        headless: bool = True,
        sync_r2: bool = True,
    ):
        self.config_path = Path(config_path)
        self.bronze_dir = Path(bronze_dir)
        self.headless = headless
        self.sync_r2 = sync_r2
        self.leagues_config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        """Load and validate config/leagues.yaml."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        # Validate leagues
        leagues = [LeagueSeason(**item) for item in data.get("leagues", [])]
        data["validated_leagues"] = leagues
        return data

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def fetch_page_content(self, page, url: str) -> str:
        """Fetch page content with automatic tenacity retries."""
        logger.debug(f"Navigating to {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        return await page.content()

    async def discover_completed_matches(
        self,
        page,
        season_id: int,
        league_id: int,
    ) -> list[dict[str, Any]]:
        """Discover all completed match IDs and metadata for a league season.
        
        Navigates to the league portal page, inspects all round options, and fetches
        the LeagueGamesList component for each round via in-browser AJAX.
        """
        league_url = f"https://www.football.org.il/leagues/league/?league_id={league_id}&season_id={season_id}"
        logger.info(f"Discovering matches for league_id={league_id}, season_id={season_id} via {league_url}")

        html = await self.fetch_page_content(page, league_url)
        tree = LexborHTMLParser(html)

        # 1. Discover round numbers from select dropdown
        round_select = tree.css_first("select.round-ajax-changer")
        round_ids: list[int] = []
        if round_select:
            for opt in round_select.css("option"):
                val = opt.attributes.get("value", "")
                if val.isdigit():
                    round_ids.append(int(val))

        if not round_ids:
            logger.warning(f"No round dropdown found for league {league_id}, defaulting to rounds 1..30")
            round_ids = list(range(1, 31))

        logger.info(f"Found {len(round_ids)} rounds for league {league_id}")

        completed_matches: list[dict[str, Any]] = []
        seen_match_ids: set[int] = set()

        # 2. Fetch LeagueGamesList component for each round
        for r_id in round_ids:
            try:
                xml_resp = await page.evaluate(f"""async () => {{
                    const url = 'https://www.football.org.il//Components.asmx/LeagueGamesList?league_id={league_id}&season_id={season_id}&box=10&round_id={r_id}&componentTitle=%D7%9E%D7%A9%D7%97%D7%A7%D7%99%D7%9D';
                    const resp = await fetch(url);
                    return await resp.text();
                }}""")

                # Parse XML and unescape HtmlData
                root = ET.fromstring(xml_resp)
                raw_html = ""
                for child in root:
                    if "HtmlData" in child.tag:
                        raw_html = html_lib.unescape(child.text or "")
                        break

                if not raw_html:
                    continue

                round_tree = LexborHTMLParser(raw_html)
                for row in round_tree.css(".table_row"):
                    # Check for game URL link
                    href = row.attributes.get("href", "")
                    if not href:
                        a_link = row.css_first("a[href*='game_id=']")
                        if a_link:
                            href = a_link.attributes.get("href", "")

                    if "game_id=" not in href:
                        continue

                    # Completed matches have score / result
                    res_col = row.css_first(".result")
                    res_text = res_col.text(strip=True) if res_col else ""
                    if not res_text and "תוצאה" not in row.text():
                        # Unplayed fixture
                        continue

                    m_match = re.search(r"game_id=(\d+)", href)
                    if not m_match:
                        continue
                    m_id = int(m_match.group(1))

                    if m_id in seen_match_ids:
                        continue
                    seen_match_ids.add(m_id)

                    # Extract teams & date
                    team1 = row.attributes.get("data-team1")
                    team2 = row.attributes.get("data-team2")
                    home_team_id = int(team1) if team1 and team1.isdigit() else None
                    away_team_id = int(team2) if team2 and team2.isdigit() else None

                    date_el = row.css_first(".game-date")
                    match_date_str = date_el.text(strip=True) if date_el else None
                    parsed_d = parse_hebrew_date(match_date_str) if match_date_str else None

                    completed_matches.append({
                        "match_id": m_id,
                        "season_id": season_id,
                        "league_id": league_id,
                        "round_number": r_id,
                        "home_team_id": home_team_id,
                        "away_team_id": away_team_id,
                        "match_date": parsed_d,
                    })
            except Exception as exc:
                logger.debug(f"Error fetching round {r_id} for league {league_id}: {exc}")
                continue

        logger.info(f"Discovered total {len(completed_matches)} completed matches for league {league_id}")
        return completed_matches

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def scrape_match_details(
        self,
        page,
        match_info: dict[str, Any],
    ) -> tuple[MatchMetadata, list[PlayerAppearance]]:
        """Scrape and parse full match details and squad appearances."""
        match_id = match_info["match_id"]
        season_id = match_info["season_id"]
        league_id = match_info["league_id"]

        url = f"https://www.football.org.il/leagues/games/game/?game_id={match_id}"
        html = await self.fetch_page_content(page, url)

        metadata, appearances, _ = parse_game_page(
            html,
            match_id=match_id,
            season_id=season_id,
            league_id=league_id,
        )

        # Enrich metadata with discovery information if available
        if metadata.round_number is None and match_info.get("round_number") is not None:
            metadata.round_number = match_info["round_number"]
        if metadata.home_team_id is None and match_info.get("home_team_id") is not None:
            metadata.home_team_id = match_info["home_team_id"]
        if metadata.away_team_id is None and match_info.get("away_team_id") is not None:
            metadata.away_team_id = match_info["away_team_id"]
        if metadata.match_date is None and match_info.get("match_date") is not None:
            metadata.match_date = match_info["match_date"]

        # Ensure appearances have season_id and league_id set
        for app in appearances:
            if app.season_id is None:
                app.season_id = season_id
            if app.league_id is None:
                app.league_id = league_id

        return metadata, appearances

    async def crawl(
        self,
        season_id: Optional[int] = None,
        league_ids: Optional[list[int]] = None,
        lookback_days: Optional[int] = None,
        dry_run: bool = False,
        run_logger: Optional[ETLRunLogger] = None,
    ) -> dict[str, Any]:
        """Execute full crawler ingestion run across specified leagues."""
        from playwright.async_api import async_playwright

        target_season = season_id or self.leagues_config.get("default_season_id", 28)
        all_leagues = self.leagues_config.get("validated_leagues", [])

        if league_ids:
            target_leagues = [l for l in all_leagues if l.league_id in league_ids]
        else:
            target_leagues = all_leagues

        if dry_run and target_leagues:
            target_leagues = target_leagues[:1]

        logger.info(
            f"Starting IFA crawler run: season_id={target_season}, "
            f"leagues={len(target_leagues)}, lookback_days={lookback_days}, dry_run={dry_run}"
        )

        total_scraped_matches = 0
        total_rows_written = 0
        newly_scraped_appearances: list[PlayerAppearance] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent=IFA_USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            for league in target_leagues:
                lid = league.league_id
                logger.info(f"Processing league {lid} ({league.name_en} / {league.name_he})")

                # Watermark check
                skip_ids = get_existing_match_watermark(
                    bronze_dir=self.bronze_dir,
                    season_id=target_season,
                    league_id=lid,
                    lookback_days=lookback_days,
                )
                logger.info(f"League {lid}: {len(skip_ids)} matches in Bronze watermark to skip")

                # Discover completed matches
                completed_matches = await self.discover_completed_matches(page, target_season, lid)

                # Filter out watermarked matches
                matches_to_fetch = [m for m in completed_matches if m["match_id"] not in skip_ids]
                logger.info(f"League {lid}: {len(matches_to_fetch)} new or updated matches to scrape")

                if dry_run:
                    matches_to_fetch = matches_to_fetch[:2]
                    logger.info(f"League {lid} [dry-run]: limited to {len(matches_to_fetch)} matches")

                league_matches: list[MatchMetadata] = []
                league_appearances: list[PlayerAppearance] = []

                for m_info in matches_to_fetch:
                    try:
                        meta, apps = await self.scrape_match_details(page, m_info)
                        league_matches.append(meta)
                        league_appearances.extend(apps)
                        newly_scraped_appearances.extend(apps)
                        total_scraped_matches += 1
                        if run_logger:
                            run_logger.increment_matches(1)
                    except Exception as exc:
                        logger.error(f"Error scraping match {m_info['match_id']}: {exc}")

                # Write Parquet for this league
                if league_matches or league_appearances:
                    m_rows, a_rows = write_matches_and_appearances(
                        bronze_dir=self.bronze_dir,
                        season_id=target_season,
                        league_id=lid,
                        matches=league_matches,
                        appearances=league_appearances,
                        sync_r2=self.sync_r2,
                    )
                    total_rows_written += (m_rows + a_rows)
                    if run_logger:
                        run_logger.increment_rows(m_rows + a_rows)
                    logger.info(f"League {lid}: written {m_rows} match rows, {a_rows} appearance rows to Bronze")

            await browser.close()

        logger.info(
            f"Crawler run complete: scraped {total_scraped_matches} matches, "
            f"written {total_rows_written} total rows across all partitions"
        )
        return {
            "matches_scraped": total_scraped_matches,
            "rows_written": total_rows_written,
            "appearances": newly_scraped_appearances,
        }


def main():
    """CLI entrypoint for ifa_crawler."""
    parser = argparse.ArgumentParser(description="IFA Bronze Lakehouse Ingestion Crawler")
    parser.add_argument("--season-id", type=int, default=None, help="IFA season ID (default from config/leagues.yaml)")
    parser.add_argument("--league-ids", type=int, nargs="*", default=None, help="Filter specific IFA league IDs")
    parser.add_argument("--lookback-days", type=int, default=None, help="Re-fetch matches from last N days for stat corrections")
    parser.add_argument("--dry-run", action="store_true", help="Limit to 2 matches per league for testing")
    parser.add_argument("--enrich-players", action="store_true", help="Automatically enrich players from newly scraped matches")
    parser.add_argument("--bronze-dir", type=str, default="data/bronze", help="Bronze storage directory")
    parser.add_argument("--headless", type=str, default=os.getenv("HEADLESS_BROWSER", "true"), help="Headless browser execution (true/false)")
    parser.add_argument("--no-r2-sync", action="store_true", help="Disable Cloudflare R2 synchronization")

    args = parser.parse_args()
    headless_bool = str(args.headless).lower() in ("true", "1", "yes")

    crawler = IFACrawler(
        bronze_dir=args.bronze_dir,
        headless=headless_bool,
        sync_r2=not args.no_r2_sync,
    )

    with ETLRunLogger(log_path=Path(args.bronze_dir) / "etl_run_logs.parquet", sync_r2=not args.no_r2_sync) as rlogger:
        result = asyncio.run(
            crawler.crawl(
                season_id=args.season_id,
                league_ids=args.league_ids,
                lookback_days=args.lookback_days,
                dry_run=args.dry_run,
                run_logger=rlogger,
            )
        )
        logger.info(f"Crawler finished successfully. Scraped {result['matches_scraped']} matches.")

        if args.enrich_players and result.get("appearances"):
            from src.ingest.player_enricher import PlayerEnricher
            logger.info("Enriching unseen players from newly scraped matches...")
            enricher = PlayerEnricher(
                bronze_dir=args.bronze_dir,
                headless=headless_bool,
                sync_r2=not args.no_r2_sync,
            )
            candidates = [
                {
                    "player_id": a.player_id,
                    "team_name": a.team_name,
                    "team_id": None,
                    "season_id": a.season_id,
                    "league_id": a.league_id,
                    "player_name": a.player_name,
                }
                for a in result["appearances"]
            ]
            asyncio.run(
                enricher.enrich_candidates(
                    candidates=candidates,
                    season_id=args.season_id or crawler.leagues_config.get("default_season_id", 28),
                    dry_run=args.dry_run,
                    run_logger=rlogger,
                )
            )


if __name__ == "__main__":
    main()
