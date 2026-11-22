"""Unit tests for automated scouting reporting layer (scout_digest.py)."""

from datetime import date
import json
from pathlib import Path
import re
from unittest.mock import MagicMock, patch
import duckdb
import pytest

from src.reporting.scout_digest import (
    CLAUDE_SYSTEM_PROMPT,
    DEFAULT_MODEL,
    REGION_NAMES_HE,
    export_pdf_digest,
    export_whatsapp_digest,
    extract_player_metrics_for_llm,
    fetch_top_prospects,
    generate_grounded_blurb_fallback,
    generate_scouting_blurbs,
    generate_single_player_blurb,
    get_duckdb_connection,
    render_html_digest,
    run_digest,
)


@pytest.fixture
def mock_warehouse_con() -> duckdb.DuckDBPyConnection:
    """Fixture providing an in-memory DuckDB warehouse with dim_leagues and mart_breakout_prospects."""
    con = duckdb.connect(":memory:")

    # Create dim_leagues
    con.execute("""
        CREATE TABLE dim_leagues (
            league_id BIGINT,
            season_id BIGINT,
            league_name_en VARCHAR,
            league_name_he VARCHAR,
            tier VARCHAR,
            category VARCHAR,
            region VARCHAR
        );
    """)

    con.execute("""
        INSERT INTO dim_leagues VALUES
        (61, 28, 'Liga Alef North', 'ליגה א'' צפון', 'Senior Tier 3', 'Senior', 'North'),
        (62, 28, 'Liga Alef South', 'ליגה א'' דרום', 'Senior Tier 3', 'Senior', 'South'),
        (71, 28, 'Liga Bet North A', 'ליגה ב'' צפון א''', 'Senior Tier 4', 'Senior', 'North'),
        (73, 28, 'Liga Bet South A', 'ליגה ב'' דרום א''', 'Senior Tier 4', 'Senior', 'South'),
        (89, 28, 'Liga Gimel Central', 'ליגה ג'' מרכז', 'Senior Tier 5', 'Senior', 'Central');
    """)

    # Create mart_breakout_prospects
    con.execute("""
        CREATE TABLE mart_breakout_prospects (
            prospect_key VARCHAR,
            player_id BIGINT,
            season_id BIGINT,
            league_id BIGINT,
            player_name VARCHAR,
            team_id BIGINT,
            team_name VARCHAR,
            league_name_en VARCHAR,
            tier VARCHAR,
            category VARCHAR,
            birth_date DATE,
            age INTEGER,
            is_u21 BOOLEAN,
            is_u23 BOOLEAN,
            appearances INTEGER,
            starts INTEGER,
            sub_appearances INTEGER,
            minutes_played INTEGER,
            goals INTEGER,
            npg_per_90 DOUBLE,
            total_team_goals INTEGER,
            team_goal_share_pct DOUBLE,
            rolling_avg_minutes_5 DOUBLE,
            rolling_minutes_5 INTEGER,
            rolling_goals_5 INTEGER,
            rolling_npg_per_90 DOUBLE,
            league_z_score DOUBLE,
            goal_share_z_score DOUBLE,
            is_u21_ironman BOOLEAN,
            is_sudden_role_breakout BOOLEAN,
            is_youth_senior_callup BOOLEAN,
            breakout_index DOUBLE,
            calculated_at TIMESTAMP
        );
    """)

    # Insert 20 mock prospects across North, South, Central
    # Prospect 1: North, high score, ironman + sudden breakout
    con.execute("""
        INSERT INTO mart_breakout_prospects VALUES
        ('k1', 101, 28, 61, 'ליאל ישראלי', 501, 'הפועל מגדל העמק', 'Liga Alef North', 'Senior Tier 3', 'Senior', '2005-04-12', 19, true, true, 10, 10, 0, 900, 8, 0.80, 18, 44.4, 90.0, 450, 5, 1.00, 2.45, 2.10, true, true, false, 92.5, current_timestamp),
        ('k2', 102, 28, 62, 'דניאל כהן', 502, 'מכבי יבנה', 'Liga Alef South', 'Senior Tier 3', 'Senior', '2004-08-20', 20, true, true, 9, 8, 1, 780, 6, 0.69, 15, 40.0, 85.0, 425, 4, 0.85, 2.10, 1.85, true, false, true, 88.0, current_timestamp),
        ('k3', 103, 28, 61, 'אורי לוי', 503, 'עירוני נשר', 'Liga Alef North', 'Senior Tier 3', 'Senior', '2005-01-15', 19, true, true, 8, 8, 0, 720, 5, 0.62, 12, 41.7, 90.0, 450, 3, 0.60, 1.95, 1.70, true, false, false, 83.5, current_timestamp),
        ('k4', 104, 28, 62, 'יונתן מזרחי', 504, 'הפועל הרצליה', 'Liga Alef South', 'Senior Tier 3', 'Senior', '2003-11-05', 21, true, true, 10, 9, 1, 810, 7, 0.78, 20, 35.0, 80.0, 400, 4, 0.90, 2.20, 1.50, false, true, false, 81.0, current_timestamp),
        ('k5', 105, 28, 71, 'איתי ברקוביץ', 505, 'צעירי טמרה', 'Liga Bet North A', 'Senior Tier 4', 'Senior', '2006-03-30', 18, true, true, 7, 7, 0, 630, 4, 0.57, 10, 40.0, 90.0, 450, 3, 0.60, 1.80, 1.60, true, false, true, 79.5, current_timestamp),
        ('k6', 106, 28, 73, 'עמית גולן', 506, 'הפועל אזור', 'Liga Bet South A', 'Senior Tier 4', 'Senior', '2004-06-14', 20, true, true, 8, 6, 2, 600, 4, 0.60, 11, 36.4, 75.0, 375, 2, 0.48, 1.65, 1.40, false, true, false, 76.0, current_timestamp),
        ('k7', 107, 28, 61, 'נועם חזן', 507, 'הפועל כפר כנא', 'Liga Alef North', 'Senior Tier 3', 'Senior', '2003-02-18', 21, true, true, 9, 9, 0, 810, 5, 0.56, 16, 31.2, 90.0, 450, 2, 0.40, 1.55, 1.20, true, false, false, 74.0, current_timestamp),
        ('k8', 108, 28, 62, 'רועי שלום', 508, 'מכבי שעריים', 'Liga Alef South', 'Senior Tier 3', 'Senior', '2005-09-09', 19, true, true, 6, 5, 1, 480, 3, 0.56, 9, 33.3, 80.0, 400, 2, 0.45, 1.50, 1.30, false, false, true, 72.5, current_timestamp),
        ('k9', 109, 28, 71, 'מוחמד זועבי', 509, 'איחוד בני שפרעם', 'Liga Bet North A', 'Senior Tier 4', 'Senior', '2004-12-01', 20, true, true, 8, 8, 0, 720, 4, 0.50, 14, 28.6, 90.0, 450, 2, 0.40, 1.40, 1.10, true, false, false, 71.0, current_timestamp),
        ('k10', 110, 28, 73, 'שגיא אלון', 510, 'מ.כ. ירושלים', 'Liga Bet South A', 'Senior Tier 4', 'Senior', '2005-07-22', 19, true, true, 7, 6, 1, 560, 3, 0.48, 10, 30.0, 80.0, 400, 2, 0.45, 1.35, 1.20, false, false, false, 68.0, current_timestamp),
        ('k11', 111, 28, 61, 'אלון פרידמן', 511, 'באקה אל גרבייה', 'Liga Alef North', 'Senior Tier 3', 'Senior', '2003-05-11', 21, true, true, 7, 6, 1, 540, 3, 0.50, 11, 27.3, 77.0, 385, 2, 0.47, 1.30, 1.05, false, false, false, 66.5, current_timestamp),
        ('k12', 112, 28, 62, 'גיא טל', 512, 'מ.ס. דימונה', 'Liga Alef South', 'Senior Tier 3', 'Senior', '2004-04-04', 20, true, true, 6, 6, 0, 540, 3, 0.50, 12, 25.0, 90.0, 450, 2, 0.40, 1.25, 0.95, false, false, false, 65.0, current_timestamp),
        ('k13', 113, 28, 71, 'עידן וקנין', 513, 'מכבי נוג''ידאת', 'Liga Bet North A', 'Senior Tier 4', 'Senior', '2005-10-10', 19, true, true, 6, 5, 1, 480, 2, 0.38, 8, 25.0, 80.0, 400, 1, 0.22, 1.10, 0.90, false, false, false, 62.0, current_timestamp),
        ('k14', 114, 28, 73, 'דור כרמי', 514, 'בית''ר קריית גת', 'Liga Bet South A', 'Senior Tier 4', 'Senior', '2004-01-25', 20, true, true, 5, 4, 1, 390, 2, 0.46, 7, 28.6, 78.0, 390, 2, 0.46, 1.15, 1.00, false, false, false, 60.5, current_timestamp),
        ('k15', 115, 28, 61, 'יואב ארצי', 515, 'הפועל רעננה', 'Liga Alef North', 'Senior Tier 3', 'Senior', '2005-03-12', 19, true, true, 5, 4, 1, 380, 2, 0.47, 8, 25.0, 76.0, 380, 1, 0.24, 1.05, 0.90, false, false, false, 58.0, current_timestamp),
        ('k16', 116, 28, 62, 'ניב סגל', 516, 'שמשון תל אביב', 'Liga Alef South', 'Senior Tier 3', 'Senior', '2004-07-07', 20, true, true, 5, 4, 1, 360, 1, 0.25, 6, 16.7, 72.0, 360, 1, 0.25, 0.80, 0.60, false, false, false, 54.0, current_timestamp),
        ('k17', 117, 28, 89, 'עומר רון', 517, 'צעירי לוד', 'Liga Gimel Central', 'Senior Tier 5', 'Senior', '2004-09-18', 20, true, true, 8, 8, 0, 720, 6, 0.75, 16, 37.5, 90.0, 450, 4, 0.80, 2.05, 1.70, false, false, false, 75.0, current_timestamp),
        ('k18', 118, 28, 61, 'תומר שטרן', 518, 'מכבי טמרה', 'Liga Alef North', 'Senior Tier 3', 'Senior', '2005-12-12', 19, true, true, 4, 4, 0, 360, 1, 0.25, 5, 20.0, 90.0, 360, 1, 0.25, 0.70, 0.50, false, false, false, 50.0, current_timestamp),
        ('k19', 119, 28, 62, 'בר סער', 519, 'עירוני מודיעין', 'Liga Alef South', 'Senior Tier 3', 'Senior', '2003-04-01', 21, true, true, 4, 3, 1, 310, 1, 0.29, 6, 16.7, 77.5, 310, 1, 0.29, 0.75, 0.55, false, false, false, 48.0, current_timestamp),
        ('k20', 120, 28, 71, 'הראל דגן', 520, 'הפועל טירת כרמל', 'Liga Bet North A', 'Senior Tier 4', 'Senior', '2005-08-08', 19, true, true, 4, 3, 1, 300, 1, 0.30, 4, 25.0, 75.0, 300, 1, 0.30, 0.80, 0.65, false, false, false, 46.0, current_timestamp);
    """)

    return con


# =============================================================================
# 1. System Prompt & Grounding Tests
# =============================================================================

class TestSystemPromptAndGrounding:
    """Verify strict constraints and zero-hallucination rules in system prompt."""

    def test_system_prompt_strictly_forbids_invented_traits(self):
        """Prompt must forbid subjective attributes (quick, vision, clinical, etc.)."""
        assert "Zero-Hallucination" in CLAUDE_SYSTEM_PROMPT or "אפס המצאות" in CLAUDE_SYSTEM_PROMPT
        assert "מהיר" in CLAUDE_SYSTEM_PROMPT
        assert "ראיית משחק" in CLAUDE_SYSTEM_PROMPT
        assert "סיומת קטלנית" in CLAUDE_SYSTEM_PROMPT
        assert "2 משפטים" in CLAUDE_SYSTEM_PROMPT

    def test_system_prompt_restricts_to_gold_metrics(self):
        """Prompt must explicitly list authorized metrics."""
        assert "minutes_played" in CLAUDE_SYSTEM_PROMPT
        assert "goals" in CLAUDE_SYSTEM_PROMPT
        assert "npg_per_90" in CLAUDE_SYSTEM_PROMPT
        assert "team_goal_share_pct" in CLAUDE_SYSTEM_PROMPT
        assert "is_u21_ironman" in CLAUDE_SYSTEM_PROMPT
        assert "Breakout Index" in CLAUDE_SYSTEM_PROMPT

    def test_extract_player_metrics_for_llm_contains_exact_fields(self):
        """Extracted JSON metrics must match strict data contract."""
        raw_player = {
            "player_id": 101,
            "player_name": "ליאל ישראלי",
            "team_name": "הפועל מגדל העמק",
            "league_name_he": "ליגה א' צפון",
            "region": "North",
            "age": 19,
            "minutes_played": 900,
            "appearances": 10,
            "starts": 10,
            "sub_appearances": 0,
            "goals": 8,
            "npg_per_90": 0.80,
            "team_goal_share_pct": 44.4,
            "total_team_goals": 18,
            "rolling_goals_5": 5,
            "rolling_npg_per_90": 1.0,
            "league_z_score": 2.45,
            "is_u21_ironman": True,
            "is_sudden_role_breakout": True,
            "is_youth_senior_callup": False,
            "breakout_index": 92.5,
            "extra_irrelevant_field": "do_not_include",
        }
        extracted = extract_player_metrics_for_llm(raw_player)
        assert extracted["player_id"] == 101
        assert extracted["player_name"] == "ליאל ישראלי"
        assert extracted["goals"] == 8
        assert extracted["is_u21_ironman"] is True
        assert "extra_irrelevant_field" not in extracted

    def test_generate_grounded_blurb_fallback(self):
        """Fallback blurb must cite exact numbers and have exactly 2 sentences in Hebrew."""
        player = {
            "player_name": "ליאל ישראלי",
            "age": 19,
            "goals": 8,
            "minutes_played": 900,
            "npg_per_90": 0.8,
            "team_goal_share_pct": 44.4,
            "team_name": "הפועל מגדל העמק",
            "breakout_index": 92.5,
            "league_z_score": 2.45,
            "is_u21_ironman": True,
            "is_sudden_role_breakout": True,
            "is_youth_senior_callup": False,
        }
        note = generate_grounded_blurb_fallback(player)
        assert "ליאל ישראלי" in note
        assert "8 שערים ב-900 דקות משחק" in note
        assert "44.4%" in note
        assert "92.5" in note
        assert "U21 איירונמן" in note
        # Check that it ends with period and has 2 sentences (separated by period followed by space)
        sentences = [s.strip() for s in re.split(r'\.\s+(?=[א-תA-Z])', note) if s.strip()]
        assert len(sentences) == 2


# =============================================================================
# 2. DuckDB Gold Layer Query & CLI Region Filter Tests
# =============================================================================

class TestDuckDBQueryAndRegionFilter:
    """Verify DuckDB querying, ordering by breakout_index, and CLI region filtering."""

    def test_fetch_top_15_all_regions(self, mock_warehouse_con):
        """Top 15 query with region='All' must return at most 15 sorted by breakout_index DESC."""
        prospects = fetch_top_prospects(mock_warehouse_con, region="All", limit=15)
        assert len(prospects) == 15
        # Verify order
        scores = [p["breakout_index"] for p in prospects]
        assert scores == sorted(scores, reverse=True)
        # Verify rank 1 is highest
        assert prospects[0]["rank"] == 1
        assert prospects[0]["player_name"] == "ליאל ישראלי"
        assert prospects[0]["breakout_index"] == 92.5
        # Badges list populated
        assert "🛡️ U21 איירונמן" in prospects[0]["badges_list"]
        assert "⚡ פריצת תפקיד" in prospects[0]["badges_list"]

    def test_fetch_top_prospects_filtered_by_north(self, mock_warehouse_con):
        """Region filter for 'North' returns only North prospects."""
        north_prospects = fetch_top_prospects(mock_warehouse_con, region="North", limit=15)
        assert len(north_prospects) > 0
        for p in north_prospects:
            assert p["region"].lower() == "north"
        # First should still be ליאל ישראלי
        assert north_prospects[0]["player_name"] == "ליאל ישראלי"

    def test_fetch_top_prospects_filtered_by_south(self, mock_warehouse_con):
        """Region filter for 'South' returns only South prospects."""
        south_prospects = fetch_top_prospects(mock_warehouse_con, region="South", limit=15)
        assert len(south_prospects) > 0
        for p in south_prospects:
            assert p["region"].lower() == "south"
        # Top South prospect in our fixture is דניאל כהן (88.0)
        assert south_prospects[0]["player_name"] == "דניאל כהן"
        assert south_prospects[0]["breakout_index"] == 88.0

    def test_fetch_top_prospects_case_insensitive(self, mock_warehouse_con):
        """Region filter should work case-insensitively ('south', 'SOUTH', 'South')."""
        p1 = fetch_top_prospects(mock_warehouse_con, region="south", limit=5)
        p2 = fetch_top_prospects(mock_warehouse_con, region="SOUTH", limit=5)
        assert [p["player_id"] for p in p1] == [p["player_id"] for p in p2]


# =============================================================================
# 3. Anthropic Claude API Mock & JSON Blurb Generation Tests
# =============================================================================

class TestAnthropicClaudeScoutingBlurbs:
    """Verify integration with Claude API using a mocked client."""

    def test_generate_scouting_blurbs_batch_success(self):
        """Claude API returns valid structured JSON mapping player_id to 2-sentence Hebrew note."""
        players = [
            {
                "player_id": 101,
                "player_name": "ליאל ישראלי",
                "team_name": "הפועל מגדל העמק",
                "league_name_he": "ליגה א' צפון",
                "region": "North",
                "age": 19,
                "minutes_played": 900,
                "appearances": 10,
                "starts": 10,
                "goals": 8,
                "npg_per_90": 0.80,
                "team_goal_share_pct": 44.4,
                "breakout_index": 92.5,
                "is_u21_ironman": True,
                "is_sudden_role_breakout": True,
                "is_youth_senior_callup": False,
            },
            {
                "player_id": 102,
                "player_name": "דניאל כהן",
                "team_name": "מכבי יבנה",
                "league_name_he": "ליגה א' דרום",
                "region": "South",
                "age": 20,
                "minutes_played": 780,
                "appearances": 9,
                "starts": 8,
                "goals": 6,
                "npg_per_90": 0.69,
                "team_goal_share_pct": 40.0,
                "breakout_index": 88.0,
                "is_u21_ironman": True,
                "is_sudden_role_breakout": False,
                "is_youth_senior_callup": True,
            },
        ]

        # Mock Claude response with JSON containing blurbs
        mock_response_json = json.dumps({
            "blurbs": [
                {
                    "player_id": 101,
                    "blurb": "ליאל ישראלי כבש 8 שערים ב-900 דקות (44.4% משערי קבוצתו) בקצב של 0.80 שערים ל-90 דקות. השחקן בן ה-19 מוביל את מדד הפריצה עם ציון 92.5 ומשלים מעל 80% מדקות הבוגרים כ-U21 איירונמן."
                },
                {
                    "player_id": 102,
                    "blurb": "דניאל כהן רשם העונה 6 שערים ב-780 דקות משחק במדי מכבי יבנה, המהווים 40.0% מסך שערי קבוצתו. הוא מדורג עם Breakout Index של 88.0 כ-U21 איירונמן ורושם השתלבות מוצלחת במעבר מנוער לבוגרים."
                }
            ]
        })

        mock_client = MagicMock()
        mock_block = MagicMock()
        mock_block.text = mock_response_json
        mock_client.messages.create.return_value.content = [mock_block]

        result = generate_scouting_blurbs(
            players=players,
            client=mock_client,
            model="claude-3-5-haiku-20241022",
        )

        assert mock_client.messages.create.called
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-3-5-haiku-20241022"
        assert call_kwargs["system"] == CLAUDE_SYSTEM_PROMPT

        # Verify blurbs were set correctly
        assert "ליאל ישראלי כבש 8 שערים" in result[0]["ai_blurb"]
        assert "דניאל כהן רשם העונה" in result[1]["ai_blurb"]

    def test_generate_single_player_blurb_call(self):
        """Test single player generation with mocked client."""
        player = {
            "player_id": 103,
            "player_name": "אורי לוי",
            "team_name": "עירוני נשר",
            "league_name_he": "ליגה א' צפון",
            "region": "North",
            "age": 19,
            "minutes_played": 720,
            "appearances": 8,
            "starts": 8,
            "goals": 5,
            "npg_per_90": 0.62,
            "team_goal_share_pct": 41.7,
            "breakout_index": 83.5,
            "is_u21_ironman": True,
            "is_sudden_role_breakout": False,
            "is_youth_senior_callup": False,
        }

        mock_client = MagicMock()
        mock_block = MagicMock()
        mock_block.text = "אורי לוי בן ה-19 כבש 5 שערים ב-720 דקות משחק המהווים 41.7% משערי עירוני נשר. הוא שומר על יציבות בהרכב כ-U21 איירונמן עם ציון פריצה של 83.5."
        mock_client.messages.create.return_value.content = [mock_block]

        blurb = generate_single_player_blurb(player, client=mock_client, model="claude-3-7-sonnet-20250219")
        assert "אורי לוי" in blurb
        assert "U21 איירונמן" in blurb
        assert mock_client.messages.create.call_args[1]["model"] == "claude-3-7-sonnet-20250219"

    def test_dry_run_generates_grounded_fallback_without_client(self):
        """In dry_run mode, blurbs are generated without invoking Claude API."""
        players = [{
            "player_id": 104,
            "player_name": "יונתן מזרחי",
            "age": 21,
            "goals": 7,
            "minutes_played": 810,
            "npg_per_90": 0.78,
            "team_goal_share_pct": 35.0,
            "team_name": "הפועל הרצליה",
            "breakout_index": 81.0,
            "league_z_score": 2.2,
            "is_u21_ironman": False,
            "is_sudden_role_breakout": True,
            "is_youth_senior_callup": False,
        }]

        result = generate_scouting_blurbs(players=players, client=None, dry_run=True)
        assert "יונתן מזרחי" in result[0]["ai_blurb"]
        assert "7 שערים ב-810 דקות" in result[0]["ai_blurb"]


# =============================================================================
# 4. RTL Hebrew Jinja2 HTML Rendering Tests
# =============================================================================

class TestHTMLRenderingAndRTLHebrew:
    """Verify templates/sunday_digest.html renders responsive RTL Hebrew format."""

    def test_render_html_contains_dir_rtl_and_hebrew_elements(self, tmp_path):
        """Rendered HTML must have dir='rtl', lang='he', Hebrew titles, badges, and blurbs."""
        prospects = [
            {
                "rank": 1,
                "player_id": 101,
                "player_name": "ליאל ישראלי",
                "team_name": "הפועל מגדל העמק",
                "league_name_he": "ליגה א' צפון",
                "tier": "Senior Tier 3",
                "region": "North",
                "age": 19,
                "is_u21": True,
                "is_u23": True,
                "appearances": 10,
                "starts": 10,
                "sub_appearances": 0,
                "minutes_played": 900,
                "goals": 8,
                "npg_per_90": 0.80,
                "team_goal_share_pct": 44.4,
                "rolling_goals_5": 5,
                "rolling_npg_per_90": 1.00,
                "league_z_score": 2.45,
                "is_u21_ironman": True,
                "is_sudden_role_breakout": True,
                "is_youth_senior_callup": False,
                "breakout_index": 92.5,
                "ai_blurb": "ליאל ישראלי רשם 8 שערים ב-900 דקות משחק (0.80 ל-90) במדי הפועל מגדל העמק. הוא מדורג ראשון עם מדד פריצה 92.5 כ-U21 איירונמן.",
                "badges_list": ["🛡️ U21 איירונמן", "⚡ פריצת תפקיד"],
            },
            {
                "rank": 2,
                "player_id": 102,
                "player_name": "דניאל כהן",
                "team_name": "מכבי יבנה",
                "league_name_he": "ליגה א' דרום",
                "tier": "Senior Tier 3",
                "region": "South",
                "age": 20,
                "is_u21": True,
                "is_u23": True,
                "appearances": 9,
                "starts": 8,
                "sub_appearances": 1,
                "minutes_played": 780,
                "goals": 6,
                "npg_per_90": 0.69,
                "team_goal_share_pct": 40.0,
                "rolling_goals_5": 4,
                "rolling_npg_per_90": 0.85,
                "league_z_score": 2.10,
                "is_u21_ironman": True,
                "is_sudden_role_breakout": False,
                "is_youth_senior_callup": True,
                "breakout_index": 88.0,
                "ai_blurb": "דניאל כהן הבקיע 6 שערים ב-780 דקות המהווים 40.0% מסך שערי מכבי יבנה. השחקן מציג מדד 88.0 עם קריאה מנוער לבוגרים.",
                "badges_list": ["🛡️ U21 איירונמן", "🚀 קריאה מנוער"],
            },
        ]

        out_html = tmp_path / "sunday_digest.html"
        html_str = render_html_digest(
            prospects=prospects,
            region="North",
            template_path="templates/sunday_digest.html",
            output_path=str(out_html),
            model_name="claude-3-5-haiku-20241022",
        )

        assert out_html.exists()
        assert 'dir="rtl"' in html_str
        assert 'lang="he"' in html_str
        assert "דוח סקאוטינג שבועי: פרוספקטים פורצים" in html_str
        assert "ליאל ישראלי" in html_str
        assert "הפועל מגדל העמק" in html_str
        assert "92.5" in html_str
        assert "🛡️ U21 איירונמן" in html_str
        assert "⚡ פריצת תפקיד פתאומית" in html_str
        assert "🚀 קריאה מנוער לבוגרים" in html_str
        assert "הערת סקאוט אנליטית" in html_str
        assert "ליאל ישראלי רשם 8 שערים ב-900 דקות משחק" in html_str

    def test_render_html_empty_prospects(self, tmp_path):
        """Empty prospects list renders graceful Hebrew empty state."""
        out_html = tmp_path / "sunday_digest_empty.html"
        html_str = render_html_digest(
            prospects=[],
            region="South",
            template_path="templates/sunday_digest.html",
            output_path=str(out_html),
        )
        assert out_html.exists()
        assert "לא נמצאו שחקנים התואמים את הקריטריונים" in html_str
        assert 'dir="rtl"' in html_str


# =============================================================================
# 5. WhatsApp / Telegram Markdown Export Tests
# =============================================================================

class TestWhatsAppTelegramExport:
    """Verify clean markdown formatting for WhatsApp/Telegram."""

    def test_whatsapp_digest_formatting(self, tmp_path):
        """Export must contain bold markers (*), emojis, stats, badges, and AI blurb."""
        prospects = [
            {
                "rank": 1,
                "player_id": 101,
                "player_name": "ליאל ישראלי",
                "team_name": "הפועל מגדל העמק",
                "league_name_he": "ליגה א' צפון",
                "region": "North",
                "age": 19,
                "minutes_played": 900,
                "goals": 8,
                "npg_per_90": 0.80,
                "team_goal_share_pct": 44.4,
                "rolling_goals_5": 5,
                "rolling_npg_per_90": 1.00,
                "league_z_score": 2.45,
                "breakout_index": 92.5,
                "badges_list": ["🛡️ U21 איירונמן", "⚡ פריצת תפקיד"],
                "ai_blurb": "ליאל ישראלי רשם 8 שערים ב-900 דקות משחק. הוא מוביל את הדירוג עם ציון 92.5.",
            }
        ]

        out_txt = tmp_path / "whatsapp_digest.txt"
        digest_text = export_whatsapp_digest(
            prospects=prospects,
            region="North",
            output_path=str(out_txt),
        )

        assert out_txt.exists()
        assert "*דוח סקאוטינג שבועי | IL-Scout-Engine*" in digest_text
        assert "*1. ליאל ישראלי* (גיל 19) | *הפועל מגדל העמק*" in digest_text
        assert "*Breakout Index:* *92.5*" in digest_text
        assert "8 שערים ב-900' (0.8 ל-90)" in digest_text
        assert "44.4% משערי הקבוצה" in digest_text
        assert "🛡️ U21 איירונמן | ⚡ פריצת תפקיד" in digest_text
        assert "ליאל ישראלי רשם 8 שערים ב-900 דקות משחק." in digest_text
        assert "il-scout-engine" in digest_text


# =============================================================================
# 6. End-to-End Pipeline & Optional PDF Tests
# =============================================================================

class TestEndToEndScoutDigest:
    """Verify end-to-end run_digest orchestrator function with mocks."""

    def test_run_digest_end_to_end_mocked(self, mock_warehouse_con, tmp_path):
        """End-to-end pipeline run from DuckDB through Claude to HTML, WhatsApp and PDF."""
        # Save in-memory database to a temp DuckDB file
        db_file = tmp_path / "temp_warehouse.duckdb"
        mock_warehouse_con.execute(f"EXPORT DATABASE '{tmp_path}/export_dir'")

        # Create new DuckDB file on disk
        file_con = duckdb.connect(str(db_file))
        file_con.execute(f"IMPORT DATABASE '{tmp_path}/export_dir'")
        file_con.close()

        # Mock Claude API client
        mock_client = MagicMock()
        mock_response = json.dumps({
            "blurbs": [
                {
                    "player_id": 101,
                    "blurb": "ליאל ישראלי בן 19 כבש 8 שערים ב-900 דקות. הוא מדורג ראשון עם מדד 92.5 כ-U21 איירונמן."
                }
            ]
        })
        mock_block = MagicMock()
        mock_block.text = mock_response
        mock_client.messages.create.return_value.content = [mock_block]

        out_dir = tmp_path / "output_test"
        result = run_digest(
            region="North",
            limit=5,
            model="claude-3-5-haiku-20241022",
            db_path=str(db_file),
            output_dir=str(out_dir),
            template_path="templates/sunday_digest.html",
            generate_pdf=False,  # Skip PDF in headless CI test
            client=mock_client,
        )

        assert result["prospects_count"] > 0
        assert Path(result["html_path"]).exists()
        assert Path(result["whatsapp_path"]).exists()
        assert Path(result["html_path"]).read_text(encoding="utf-8").startswith("<!DOCTYPE html>")

    def test_export_pdf_failsafe(self, tmp_path):
        """Optional PDF export should not raise exception if weasyprint and chromium are unavailable."""
        out_pdf = tmp_path / "test.pdf"
        # Mock failure of weasyprint and playwright
        with patch.dict("sys.modules", {"weasyprint": None, "playwright": None}):
            success = export_pdf_digest("<html><body>Test</body></html>", output_path=str(out_pdf))
            # Should return False and not raise
            assert success is False
