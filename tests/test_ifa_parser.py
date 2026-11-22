"""Unit and integration tests for IFA DOM parser and Pydantic schemas."""

from datetime import date
from pathlib import Path
import pytest
import yaml

from src.parsers.ifa_parser import IFAParser, parse_game_page, parse_player_page
from src.schemas import (
    LeagueSeason,
    MatchEvent,
    MatchMetadata,
    PlayerAppearance,
    PlayerProfile,
    parse_hebrew_date,
    parse_hebrew_minute,
    parse_shirt_number,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_game_html() -> str:
    """Fixture providing raw HTML content of an IFA match."""
    game_file = FIXTURES_DIR / "ifl_example_game.html"
    assert game_file.exists(), f"Fixture file not found: {game_file}"
    return game_file.read_text(encoding="utf-8")


@pytest.fixture
def sample_player_html() -> str:
    """Fixture providing raw HTML content of an IFA player profile."""
    player_file = FIXTURES_DIR / "ifl_example_player.html"
    assert player_file.exists(), f"Fixture file not found: {player_file}"
    return player_file.read_text(encoding="utf-8")


# =========================================================================
# 1. Pydantic v2 Schema & Hebrew Parsing Tests
# =========================================================================

class TestHebrewParsersAndSchemas:
    """Tests for Hebrew string normalization and Pydantic validation."""

    def test_parse_hebrew_minute(self):
        """Verify parsing various Hebrew minute representations."""
        assert parse_hebrew_minute(64) == 64
        assert parse_hebrew_minute("64") == 64
        assert parse_hebrew_minute("דקה 64") == 64
        assert parse_hebrew_minute("דקה64") == 64
        assert parse_hebrew_minute("64דק'") == 64
        assert parse_hebrew_minute("64 דק'") == 64
        assert parse_hebrew_minute("יצאדקה83") == 83
        assert parse_hebrew_minute("נכנסדקה46") == 46
        assert parse_hebrew_minute("כרטיס צהובדקה32") == 32
        assert parse_hebrew_minute(None) is None
        assert parse_hebrew_minute("") is None

    def test_parse_hebrew_date(self):
        """Verify parsing various date formats from the portal."""
        assert parse_hebrew_date("12/05/2006") == date(2006, 5, 12)
        assert parse_hebrew_date("18/09/2026 | 11:00") == date(2026, 9, 18)
        assert parse_hebrew_date("05/2000") == date(2000, 5, 1)
        assert parse_hebrew_date("2026-09-18") == date(2026, 9, 18)
        assert parse_hebrew_date(date(2026, 9, 18)) == date(2026, 9, 18)
        assert parse_hebrew_date(None) is None
        assert parse_hebrew_date("") is None

    def test_parse_shirt_number(self):
        """Verify parsing shirt number strings."""
        assert parse_shirt_number("מס' 11") == 11
        assert parse_shirt_number("מס' 1") == 1
        assert parse_shirt_number(11) == 11
        assert parse_shirt_number(None) is None

    def test_match_event_schema_hebrew_minute(self):
        """Verify MatchEvent validates Hebrew minute strings."""
        event = MatchEvent(
            match_id=1138999,
            event_seq=1,
            event_type="goal",
            minute="דקה 64",  # string with Hebrew prefix
            player_name="אדיר מדלסי",
            shirt_number="מס' 9",
        )
        assert event.minute == 64
        assert event.shirt_number == 9
        assert event.event_type == "goal"

    def test_player_profile_schema_hebrew_date(self):
        """Verify PlayerProfile validates Hebrew/slash date format."""
        profile = PlayerProfile(
            player_id=247094,
            full_name="אטקלט ישראל נגה",
            birth_date="12/05/2006",
            nationality="ישראל",
        )
        assert profile.birth_date == date(2006, 5, 12)
        assert profile.nationality == "ישראל"

    def test_invalid_minute_raises_error(self):
        """Verify unparseable minute string raises ValueError."""
        with pytest.raises(ValueError):
            parse_hebrew_minute("דקה לא חוקית")

    def test_invalid_date_raises_error(self):
        """Verify unparseable date string raises ValueError."""
        with pytest.raises(ValueError):
            parse_hebrew_date("תאריך לא חוקי")

    def test_strict_extra_forbidden(self):
        """Verify extra attributes are forbidden across models."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LeagueSeason(
                league_id=61,
                season_id=28,
                tier="Senior Tier 3",
                name_en="Liga Alef North",
                name_he="ליגה א' צפון",
                unknown_field="extra",
            )


# =========================================================================
# 2. Game HTML Parsing Tests (ifl_example_game.html)
# =========================================================================

class TestIFAGameParser:
    """Tests parsing a full IFA match report."""

    def test_parse_match_metadata(self, sample_game_html):
        """Verify metadata extraction from game HTML."""
        metadata, appearances, events = parse_game_page(sample_game_html)

        assert metadata.match_id == 1138999
        assert metadata.home_team_name == "עוצמה באר שבע"
        assert metadata.away_team_name == "מ.כ. באר שבע שועה טל"
        assert metadata.match_date == date(2026, 9, 18)
        assert metadata.match_time == "11:00"
        assert metadata.home_score == 0
        assert metadata.away_score == 11
        assert metadata.home_half_time_score == 0
        assert metadata.away_half_time_score == 7
        assert metadata.stadium == "באר שבע רייסר 4 סינטטי"
        assert metadata.referee == "הוידה עוז"

    def test_parse_appearances_count_and_types(self, sample_game_html):
        """Verify all 35 match appearances (starters, subs, bench) are correctly extracted."""
        _, appearances, _ = parse_game_page(sample_game_html)

        assert len(appearances) == 35

        home_apps = [a for a in appearances if a.team_side == "home"]
        away_apps = [a for a in appearances if a.team_side == "away"]

        assert len(home_apps) == 17  # 11 starters + 4 subs + 2 bench
        assert len(away_apps) == 18  # 11 starters + 5 subs + 2 bench

        starters = [a for a in appearances if a.is_starter]
        assert len(starters) == 22  # 11 home + 11 away

    def test_parse_specific_players(self, sample_game_html):
        """Verify granular player details (minutes played, goals, cards, roles)."""
        _, appearances, _ = parse_game_page(sample_game_html)
        app_by_id = {a.player_id: a for a in appearances}

        # 1. Home Goalkeeper
        gk = app_by_id.get(291129)
        assert gk is not None
        assert gk.player_name == "עילי דומר"
        assert gk.is_goalkeeper is True
        assert gk.is_starter is True
        assert gk.shirt_number == 1
        assert gk.minutes_played == 90

        # 2. Away top scorer: Adir Medalsi (4 goals)
        scorer = app_by_id.get(74621)
        assert scorer is not None
        assert scorer.player_name == "אדיר מדלסי"
        assert scorer.goals_scored == 4
        assert scorer.goal_minutes == [10, 34, 80, 91]
        assert scorer.minutes_played == 90

        # 3. Away starter subbed out: David Swisa (3 goals, subbed out at min 68)
        swisa = app_by_id.get(50273)
        assert swisa is not None
        assert swisa.player_name == "דוד סויסה"
        assert swisa.goals_scored == 3
        assert swisa.goal_minutes == [20, 21, 43]
        assert swisa.sub_out_minute == 68
        assert swisa.minutes_played == 68

        # 4. Yellow card recipient: Yunes Wasiya (Yellow at min 32)
        card_player = app_by_id.get(286190)
        assert card_player is not None
        assert card_player.player_name == "יונס ווסיה"
        assert card_player.yellow_card_minute == 32

        # 5. Substitute who came on: Ofek Al-Moalem (in at min 46)
        sub_player = app_by_id.get(203779)
        assert sub_player is not None
        assert sub_player.player_name == "אופק אלמעלם"
        assert sub_player.is_starter is False
        assert sub_player.sub_in_minute == 46
        assert sub_player.minutes_played == 44  # 90 - 46

        # 6. Unused bench player: Nadir Rada (0 minutes)
        bench_player = app_by_id.get(286705)
        assert bench_player is not None
        assert bench_player.player_name == "נדיר רדה"
        assert bench_player.minutes_played == 0

    def test_parse_match_events(self, sample_game_html):
        """Verify timeline events parsing (11 goals, cards, substitutions)."""
        _, appearances, events = parse_game_page(sample_game_html)

        assert len(events) > 0

        # Filter goal events
        goal_events = [e for e in events if e.event_type == "goal"]
        assert len(goal_events) == 11
        assert [g.minute for g in goal_events] == [10, 20, 21, 34, 35, 43, 45, 53, 75, 80, 91]

        # Verify first goal details
        first_goal = goal_events[0]
        assert first_goal.player_name == "אדיר מדלסי"
        assert first_goal.shirt_number == 9
        assert first_goal.minute == 10
        assert first_goal.player_id == 74621

        # Yellow cards
        yellow_events = [e for e in events if e.event_type == "yellow_card"]
        assert len(yellow_events) == 1
        assert yellow_events[0].player_name == "יונס ווסיה"
        assert yellow_events[0].minute == 32
        assert yellow_events[0].player_id == 286190

        # Substitutions
        sub_events = [e for e in events if e.event_type == "substitution"]
        assert len(sub_events) >= 7
        first_sub = sub_events[0]
        assert first_sub.minute == 46
        assert first_sub.player_name == "שליו פרץ"
        assert first_sub.sub_in_player_name == "אופק אלמעלם"


# =========================================================================
# 3. Player HTML Parsing Tests (ifl_example_player.html)
# =========================================================================

class TestIFAPlayerParser:
    """Tests parsing a player profile page."""

    def test_parse_player_profile(self, sample_player_html):
        """Verify player demographics and club details extraction."""
        profile = parse_player_page(sample_player_html)

        assert profile.full_name == "אטקלט ישראל נגה"
        assert profile.birth_date == date(2000, 5, 1)
        assert profile.nationality == "ישראל"
        assert profile.current_team_name == "מ.כ. באר שבע שועה טל"
        assert profile.current_league_name == "ג דרום"
        assert profile.stats is not None
        assert profile.stats.get("league_goals") == 4
        assert profile.stats.get("yellow_cards") == 1


# =========================================================================
# 4. Leagues Configuration Validation Tests
# =========================================================================

class TestLeaguesConfig:
    """Tests ensuring config/leagues.yaml complies with project requirements."""

    def test_leagues_yaml_contents(self):
        """Verify the 10 target lower/youth Israeli leagues are defined."""
        config_path = Path("config/leagues.yaml")
        assert config_path.exists(), "config/leagues.yaml must exist"

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        leagues_data = data.get("leagues", [])
        assert len(leagues_data) == 10, f"Expected 10 leagues, found {len(leagues_data)}"

        # Validate each league with the Pydantic LeagueSeason model
        validated_leagues = [LeagueSeason(**item) for item in leagues_data]
        assert len(validated_leagues) == 10

        # Check required leagues presence
        league_names_en = {l.name_en for l in validated_leagues}
        expected_names = {
            "Liga Alef North",
            "Liga Alef South",
            "Liga Bet North A",
            "Liga Bet North B",
            "Liga Bet South A",
            "Liga Bet South B",
            "Liga Leumit LaNoar North",
            "Liga Leumit LaNoar South",
            "Liga Gimel Central",
            "Liga Gimel South",
        }
        assert expected_names == league_names_en
