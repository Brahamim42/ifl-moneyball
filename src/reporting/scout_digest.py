"""Automated scouting reporting layer for il-scout-engine.

Connects to DuckDB/MotherDuck Gold layer, queries top 15 breakout prospects,
generates strictly grounded 2-sentence Hebrew scouting blurbs via Anthropic Claude API,
and renders:
1. templates/sunday_digest.html (Responsive RTL Hebrew report)
2. output/whatsapp_digest.txt (Clean WhatsApp/Telegram markdown summary)
3. output/sunday_digest.pdf (Optional PDF via weasyprint or Playwright Chromium)
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Optional

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb
from jinja2 import Environment, FileSystemLoader

try:
    import anthropic
except ImportError:
    anthropic = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scout_digest")

DEFAULT_MODEL = "claude-3-5-haiku-20241022"
DEFAULT_DB_PATH = "data/silver/scout_warehouse.duckdb"
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_TEMPLATE_PATH = "templates/sunday_digest.html"

REGION_NAMES_HE = {
    "all": "כל האזורים",
    "north": "מחוז צפון",
    "south": "מחוז דרום",
    "central": "מחוז מרכז",
}

# Strict zero-hallucination system prompt for Hebrew scouting notes
CLAUDE_SYSTEM_PROMPT = """אתה אנליסט דאטה וסקאוט מקצועי עבור מחלקות סקאוטינג בכדורגל הישראלי (ליגות נמוכות ונוער).
תפקידך לכתוב הערת סקאוטינג מדויקת, תמציתית ומקצועית בת 2 משפטים בדיוק בעברית (Hebrew) עבור פרוספקט צעיר ופורץ (breakout prospect).

כללים קריטיים ובלתי ניתנים להפרה (מדיניות אפס המצאות - Zero-Hallucination):
1. ביסוס מלא (100% Grounding): כל נתון, עובדה ומשפט חייבים להיות מבוססים במדויק ובאופן מלא על המדדים המופיעים ב-JSON בלבד.
2. איסור מוחלט על תכונות סובייקטיביות: אסור לחלוטין להמציא סגנון משחק או תכונות שלא מופיעות בנתונים (כגון: "מהיר", "שליטה נהדרת בכדור", "משחק ראש", "ראיית משחק", "סיומת קטלנית", "חזק", "כושר גבוה", "מנהיג").
3. מדדים מותרים לציון:
   - גיל (age), קבוצה (team_name), ליגה (league_name_he) ומחוז (region).
   - דקות משחק (minutes_played), הופעות והרכבים (appearances, starts, sub_appearances).
   - שערים (goals) וקצב הבקעה ל-90 דקות (npg_per_90).
   - תרומה לקבוצה: אחוז משערי הקבוצה (team_goal_share_pct) וסך שערי קבוצה (total_team_goals).
   - מגמה ב-5 משחקים אחרונים: rolling_goals_5, rolling_npg_per_90.
   - מיקום יחסי: ציון תקן הבקעה בליגה (league_z_score).
   - תגי פריצה:
     * is_u21_ironman: שחקן עד גיל 21 שמשלים מעל 80% מדקות הבוגרים הזמינות.
     * is_sudden_role_breakout: פריצת תפקיד פתאומית (עלייה חדה בדקות או הבקעות רצופות מהספסל).
     * is_youth_senior_callup: שחקן נוער שזומן ושותף בליגות הבוגרים.
   - ציון פריצה משוקלל: מדד Breakout Index (בסולם 0-100).
4. פורמט הפלט:
   - בדיוק 2 משפטים בעברית ברורה ותמציתית.
   - משפט ראשון: תפוקת השחקן במספרים (שערים, דקות, קצב ל-90, או אחוז משערי הקבוצה).
   - משפט שני: מעמד השחקן, משמעות תג הפריצה הייחודי ומדד ה-Breakout Index שלו.
   - ללא ברכות פתיחה, ללא סימני קריאה מיותרים, ללא מרכאות סביב הטקסט, וללא כוכביות markdown בגוף המשפט."""


def get_duckdb_connection(
    db_path: Optional[str] = None,
    motherduck: bool = False,
    read_only: bool = True,
) -> duckdb.DuckDBPyConnection:
    """Connect to local DuckDB or cloud MotherDuck Gold warehouse."""
    motherduck_token = os.getenv("MOTHERDUCK_TOKEN")
    use_motherduck = motherduck or bool(motherduck_token and os.getenv("DBT_TARGET") == "prod")

    if use_motherduck:
        md_database = os.getenv("MOTHERDUCK_DATABASE", "md:il_scout_engine")
        logger.info(f"Connecting to MotherDuck database: {md_database}")
        connect_str = md_database
        if motherduck_token and "motherduck_token=" not in connect_str:
            con = duckdb.connect(connect_str)
            con.execute(f"SET motherduck_token='{motherduck_token}'")
            return con
        return duckdb.connect(connect_str)

    target_path = db_path or os.getenv("DUCKDB_PATH", DEFAULT_DB_PATH)
    path_obj = Path(target_path)
    if not path_obj.is_absolute():
        path_obj = _REPO_ROOT / path_obj

    if not path_obj.exists():
        logger.warning(f"DuckDB file not found at {path_obj}, connecting in-memory or creating new")

    logger.info(f"Connecting to DuckDB: {path_obj}")
    try:
        return duckdb.connect(str(path_obj), read_only=read_only and path_obj.exists())
    except Exception as e:
        err_msg = str(e)
        if "version number" in err_msg or "newer version" in err_msg or "older version" in err_msg:
            raise RuntimeError(
                f"\n{'='*70}\n"
                f"[DuckDB Version Mismatch]\n"
                f"Your active Python environment is using DuckDB {duckdb.__version__},\n"
                f"but the warehouse database at '{path_obj}' was created with DuckDB 2.0.0.\n\n"
                f"👉 Fix: Run using the repository's virtual environment:\n"
                f"  .\\.venv\\Scripts\\python.exe src/reporting/scout_digest.py --region All\n"
                f"  Or activate the venv first:\n"
                f"  .\\.venv\\Scripts\\Activate.ps1\n"
                f"  python src/reporting/scout_digest.py --region All\n"
                f"{'='*70}"
            ) from e
        raise


def fetch_top_prospects(
    con: duckdb.DuckDBPyConnection,
    region: str = "All",
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Query top breakout prospects from mart_breakout_prospects with region filtering."""
    region_filter = region.strip().lower()
    has_region_filter = region_filter != "all"

    # Check if dim_leagues exists in warehouse to join region
    tables = [row[0] for row in con.execute("SHOW TABLES").fetchall()]
    has_dim_leagues = "dim_leagues" in tables

    if has_dim_leagues:
        base_query = """
            SELECT
                p.prospect_key,
                p.player_id,
                p.season_id,
                p.league_id,
                p.player_name,
                p.team_id,
                p.team_name,
                p.league_name_en,
                p.tier,
                p.category,
                p.birth_date,
                p.age,
                p.is_u21,
                p.is_u23,
                p.appearances,
                p.starts,
                p.sub_appearances,
                p.minutes_played,
                p.goals,
                p.npg_per_90,
                p.total_team_goals,
                p.team_goal_share_pct,
                p.rolling_avg_minutes_5,
                p.rolling_minutes_5,
                p.rolling_goals_5,
                p.rolling_npg_per_90,
                p.league_z_score,
                p.goal_share_z_score,
                p.is_u21_ironman,
                p.is_sudden_role_breakout,
                p.is_youth_senior_callup,
                p.breakout_index,
                COALESCE(l.region, 'Unknown') as region,
                COALESCE(l.league_name_he, p.league_name_en) as league_name_he
            FROM mart_breakout_prospects p
            LEFT JOIN dim_leagues l ON p.league_id = l.league_id
        """
        if has_region_filter:
            base_query += " WHERE LOWER(COALESCE(l.region, '')) = ?"
            params = [region_filter, limit]
            order_clause = " ORDER BY p.breakout_index DESC, p.minutes_played DESC, p.goals DESC LIMIT ?"
            query = base_query + order_clause
            cursor = con.execute(query, params)
        else:
            order_clause = " ORDER BY p.breakout_index DESC, p.minutes_played DESC, p.goals DESC LIMIT ?"
            query = base_query + order_clause
            cursor = con.execute(query, [limit])
    else:
        # Fallback if only mart_breakout_prospects is loaded
        base_query = "SELECT * FROM mart_breakout_prospects"
        order_clause = " ORDER BY breakout_index DESC, minutes_played DESC, goals DESC LIMIT ?"
        query = base_query + order_clause
        cursor = con.execute(query, [limit])

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    prospects: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        player = dict(zip(columns, row))
        player["rank"] = rank

        # Ensure numeric values are formatted nicely
        player["breakout_index"] = float(round(player.get("breakout_index") or 0.0, 1))
        player["npg_per_90"] = float(round(player.get("npg_per_90") or 0.0, 2))
        player["team_goal_share_pct"] = float(round(player.get("team_goal_share_pct") or 0.0, 1))
        player["league_z_score"] = float(round(player.get("league_z_score") or 0.0, 2))
        player["rolling_npg_per_90"] = float(round(player.get("rolling_npg_per_90") or 0.0, 2))
        player["rolling_goals_5"] = int(player.get("rolling_goals_5") or 0)
        player["minutes_played"] = int(player.get("minutes_played") or 0)
        player["appearances"] = int(player.get("appearances") or 0)
        player["starts"] = int(player.get("starts") or 0)
        player["sub_appearances"] = int(player.get("sub_appearances") or 0)
        player["goals"] = int(player.get("goals") or 0)
        player["region"] = player.get("region") or "Unknown"
        player["league_name_he"] = player.get("league_name_he") or player.get("league_name_en") or "ליגה ישראלית"

        # Build list of badge tags
        badges_list = []
        if player.get("is_u21_ironman"):
            badges_list.append("🛡️ U21 איירונמן")
        if player.get("is_sudden_role_breakout"):
            badges_list.append("⚡ פריצת תפקיד")
        if player.get("is_youth_senior_callup"):
            badges_list.append("🚀 קריאה מנוער")
        player["badges_list"] = badges_list

        prospects.append(player)

    logger.info(f"Retrieved {len(prospects)} breakout prospects (region={region}, limit={limit})")
    return prospects


def extract_player_metrics_for_llm(player: dict[str, Any]) -> dict[str, Any]:
    """Serialize only the exact structured metrics to JSON for Claude prompt."""
    return {
        "player_id": player.get("player_id"),
        "player_name": player.get("player_name"),
        "team_name": player.get("team_name"),
        "league_name_he": player.get("league_name_he"),
        "region": player.get("region"),
        "age": player.get("age"),
        "minutes_played": player.get("minutes_played"),
        "appearances": player.get("appearances"),
        "starts": player.get("starts"),
        "sub_appearances": player.get("sub_appearances"),
        "goals": player.get("goals"),
        "npg_per_90": player.get("npg_per_90"),
        "team_goal_share_pct": player.get("team_goal_share_pct"),
        "total_team_goals": player.get("total_team_goals"),
        "rolling_goals_5": player.get("rolling_goals_5"),
        "rolling_npg_per_90": player.get("rolling_npg_per_90"),
        "league_z_score": player.get("league_z_score"),
        "is_u21_ironman": bool(player.get("is_u21_ironman")),
        "is_sudden_role_breakout": bool(player.get("is_sudden_role_breakout")),
        "is_youth_senior_callup": bool(player.get("is_youth_senior_callup")),
        "breakout_index": player.get("breakout_index"),
    }


def generate_grounded_blurb_fallback(player: dict[str, Any]) -> str:
    """Generate a strictly grounded, deterministic 2-sentence Hebrew scouting note.
    
    Used when running in offline mode (--dry-run) or if API key is not configured.
    Guaranteed zero hallucinated traits, 100% numbers-driven.
    """
    name = player.get("player_name", "השחקן")
    age = player.get("age", 20)
    goals = player.get("goals", 0)
    minutes = player.get("minutes_played", 0)
    npg = player.get("npg_per_90", 0.0)
    share = player.get("team_goal_share_pct", 0.0)
    team = player.get("team_name", "קבוצתו")
    score = player.get("breakout_index", 0.0)
    z_score = player.get("league_z_score", 0.0)

    # Sentence 1: Attacking output / minutes ratio
    s1 = (
        f"{name} (גיל {age}) רשם העונה {goals} שערים ב-{minutes} דקות משחק במדי {team}, "
        f"המהווים {share}% מסך שערי הקבוצה ובקצב של {npg} שערים ל-90 דקות."
    )

    # Sentence 2: Status, special breakout flag & breakout index
    flag_details = []
    if player.get("is_u21_ironman"):
        flag_details.append("משלים מעל 80% מדקות הבוגרים כ-U21 איירונמן")
    if player.get("is_sudden_role_breakout"):
        flag_details.append("רושם פריצת תפקיד חדה בדקות האחרונות")
    if player.get("is_youth_senior_callup"):
        flag_details.append("משתלב בהצלחה בקריאה מליגת הנוער לבוגרים")

    if flag_details:
        flags_str = " ו" + " וכן ".join(flag_details)
    else:
        flags_str = f" עם ציון תקן הבקעה של {z_score} ביחס לליגה"

    s2 = f"הוא מוביל את דירוג הפורצים עם מדד Breakout Index של {score}{flags_str}."

    return f"{s1} {s2}"


def generate_single_player_blurb(
    player: dict[str, Any],
    client: Any,
    model: str = DEFAULT_MODEL,
) -> str:
    """Send single player structured metrics to Claude API with strict system prompt."""
    metrics = extract_player_metrics_for_llm(player)
    payload_json = json.dumps(metrics, ensure_ascii=False, indent=2)

    user_prompt = (
        f"נתוני השחקן בפורמט JSON:\n{payload_json}\n\n"
        "כתוב הערת סקאוטינג מקצועית בת 2 משפטים בדיוק בעברית המעוגנת ב-100% בנתונים לעיל ללא תכונות מומצאות."
    )

    response = client.messages.create(
        model=model,
        system=CLAUDE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=300,
        temperature=0.2,
    )

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text
        elif isinstance(block, dict) and "text" in block:
            text += block["text"]

    cleaned = text.strip().strip('"').strip("'")
    return cleaned


def generate_batch_player_blurbs(
    players: list[dict[str, Any]],
    client: Any,
    model: str = DEFAULT_MODEL,
) -> dict[int, str]:
    """Send structured batch of players to Claude API to generate Hebrew blurbs in one call."""
    batch_metrics = [extract_player_metrics_for_llm(p) for p in players]
    payload_json = json.dumps(batch_metrics, ensure_ascii=False, indent=2)

    user_prompt = (
        f"רשימת הפרוספקטים בפורמט JSON:\n{payload_json}\n\n"
        "עבור כל שחקן ברשימה, כתוב הערת סקאוטינג בת 2 משפטים בדיוק בעברית, מעוגנת 100% במספרים ללא תכונות מומצאות.\n"
        "החזר אך ורק אובייקט JSON תקין במבנה הבא ללא טקסט נוסף:\n"
        "{\n"
        '  "blurbs": [\n'
        '    {"player_id": 12345, "blurb": "משפט 1. משפט 2."}\n'
        "  ]\n"
        "}"
    )

    response = client.messages.create(
        model=model,
        system=CLAUDE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=2500,
        temperature=0.2,
    )

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text
        elif isinstance(block, dict) and "text" in block:
            text += block["text"]

    result_map: dict[int, str] = {}
    cleaned_text = text.strip()

    # Strip markdown code fences if present
    if cleaned_text.startswith("```"):
        lines = cleaned_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned_text = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned_text)
        if isinstance(data, dict):
            if "blurbs" in data and isinstance(data["blurbs"], list):
                for item in data["blurbs"]:
                    pid = item.get("player_id")
                    blurb = item.get("blurb")
                    if pid is not None and blurb:
                        result_map[int(pid)] = str(blurb).strip()
            else:
                for k, v in data.items():
                    try:
                        result_map[int(k)] = str(v).strip()
                    except (ValueError, TypeError):
                        pass
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "player_id" in item and "blurb" in item:
                    result_map[int(item["player_id"])] = str(item["blurb"]).strip()
    except Exception as e:
        logger.warning(f"Failed to parse Claude batch JSON output: {e}. Raw response: {cleaned_text[:200]}")

    return result_map


def generate_scouting_blurbs(
    players: list[dict[str, Any]],
    client: Optional[Any] = None,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Populate strictly grounded 2-sentence Hebrew scouting blurbs on each player record."""
    if not players:
        return players

    # Check whether we can/should call Claude API
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if dry_run or (client is None and not api_key):
        if not dry_run and not api_key:
            logger.warning("ANTHROPIC_API_KEY not found in environment; generating grounded fallback notes")
        for p in players:
            p["ai_blurb"] = generate_grounded_blurb_fallback(p)
        return players

    # Instantiate Anthropic client if needed
    if client is None:
        if anthropic is None:
            raise ImportError(
                "Anthropic library is required to call Claude API. Install via 'pip install anthropic' or use --dry-run."
            )
        client = anthropic.Anthropic(api_key=api_key)

    logger.info(f"Generating AI scouting notes via Claude ({model}) for {len(players)} players...")

    # First attempt batch generation
    blurb_map: dict[int, str] = {}
    try:
        blurb_map = generate_batch_player_blurbs(players, client=client, model=model)
    except Exception as e:
        logger.warning(f"Batch generation error: {e}. Falling back to single-player requests.")

    # Fill in any missing blurbs via single calls or fallback
    for p in players:
        pid = p.get("player_id")
        if pid in blurb_map and blurb_map[pid]:
            p["ai_blurb"] = blurb_map[pid]
        else:
            try:
                p["ai_blurb"] = generate_single_player_blurb(p, client=client, model=model)
            except Exception as e:
                logger.error(f"Failed to generate blurb for player {pid} ({p.get('player_name')}): {e}")
                p["ai_blurb"] = generate_grounded_blurb_fallback(p)

    return players


def render_html_digest(
    prospects: list[dict[str, Any]],
    region: str = "All",
    template_path: str = DEFAULT_TEMPLATE_PATH,
    output_path: str = "output/sunday_digest.html",
    model_name: str = "",
) -> str:
    """Render the Jinja2 RTL Hebrew template to an HTML report file."""
    tmpl_file = Path(template_path)
    if not tmpl_file.is_absolute():
        tmpl_file = _REPO_ROOT / tmpl_file

    env = Environment(
        loader=FileSystemLoader(str(tmpl_file.parent)),
        autoescape=True,
    )
    template = env.get_template(tmpl_file.name)

    # Compute high-level metadata & summary metrics
    region_lower = region.strip().lower()
    region_he = REGION_NAMES_HE.get(region_lower, f"מחוז {region}")

    avg_breakout = (
        round(sum(p.get("breakout_index", 0.0) for p in prospects) / len(prospects), 1)
        if prospects
        else 0.0
    )

    top_scorer = max(prospects, key=lambda x: x.get("goals", 0)) if prospects else None
    ironman_count = sum(1 for p in prospects if p.get("is_u21_ironman"))

    metadata = {
        "region_filter": region,
        "region_he": region_he,
        "generated_at": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC"),
        "total_prospects": len(prospects),
        "avg_breakout_index": avg_breakout,
        "top_scorer_name": top_scorer.get("player_name", "-") if top_scorer else "-",
        "top_scorer_goals": top_scorer.get("goals", 0) if top_scorer else 0,
        "ironman_count": ironman_count,
        "model_name": model_name,
    }

    rendered_html = template.render(
        prospects=prospects,
        metadata=metadata,
    )

    out_file = Path(output_path)
    if not out_file.is_absolute():
        out_file = _REPO_ROOT / out_file
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(rendered_html, encoding="utf-8")

    logger.info(f"Rendered RTL HTML scouting digest to: {out_file}")
    return rendered_html


def export_whatsapp_digest(
    prospects: list[dict[str, Any]],
    region: str = "All",
    output_path: str = "output/whatsapp_digest.txt",
) -> str:
    """Export clean WhatsApp/Telegram markdown text summary."""
    region_lower = region.strip().lower()
    region_he = REGION_NAMES_HE.get(region_lower, f"מחוז {region}")
    now_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    lines = [
        "⚽ *דוח סקאוטינג שבועי | IL-Scout-Engine*",
        f"📅 *תאריך:* {now_str} | *מחוז:* {region_he}",
        f"🎯 *טופ {len(prospects)} פרוספקטים פורצים (U21/U23)*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for p in prospects:
        rank = p.get("rank", 1)
        name = p.get("player_name", "שחקן")
        age = p.get("age", 20)
        team = p.get("team_name", "קבוצה")
        league = p.get("league_name_he", "ליגה")
        reg = p.get("region", "")
        score = p.get("breakout_index", 0.0)
        goals = p.get("goals", 0)
        mins = p.get("minutes_played", 0)
        npg = p.get("npg_per_90", 0.0)
        share = p.get("team_goal_share_pct", 0.0)
        r_goals = p.get("rolling_goals_5", 0)
        r_npg = p.get("rolling_npg_per_90", 0.0)
        z_score = p.get("league_z_score", 0.0)
        blurb = p.get("ai_blurb", "")
        badges = p.get("badges_list", [])

        lines.append(f"*{rank}. {name}* (גיל {age}) | *{team}*")
        lines.append(f"🏆 *ליגה:* {league} ({reg})")
        lines.append(f"🔥 *Breakout Index:* *{score}*")
        lines.append(f"📊 *מדדים:* {goals} שערים ב-{mins}' ({npg} ל-90) | {share}% משערי הקבוצה")
        lines.append(f"⚡ *מגמה (5 אחרונים):* {r_goals} שערים ({r_npg}/90) | Z-Score: {z_score}")

        if badges:
            lines.append(f"🏷️ *תגים:* {' | '.join(badges)}")

        lines.append("🤖 *הערת סקאוט AI:*")
        lines.append(f"{blurb}")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

    lines.append("📌 *הופק אוטומטית ע\"י שכבת ה-Gold של מנוע הסקאוטינג הישראלי (il-scout-engine)*")

    digest_text = "\n".join(lines)

    out_file = Path(output_path)
    if not out_file.is_absolute():
        out_file = _REPO_ROOT / out_file
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(digest_text, encoding="utf-8")

    logger.info(f"Exported WhatsApp digest to: {out_file}")
    return digest_text


def _generate_pdf_playwright(
    html_content: str,
    out_file: Path,
    html_file_path: Optional[str] = None,
) -> bool:
    """Internal helper to render PDF with Playwright Chromium."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=5000)
        page = browser.new_page()
        if html_file_path and Path(html_file_path).exists():
            file_url = Path(html_file_path).resolve().as_uri()
            page.goto(file_url, wait_until="load", timeout=5000)
        else:
            page.set_content(html_content, wait_until="load", timeout=5000)
        page.pdf(path=str(out_file), format="A4", print_background=True)
        browser.close()
    return True


def export_pdf_digest(
    html_content: str,
    output_path: str = "output/sunday_digest.pdf",
    html_file_path: Optional[str] = None,
    timeout_seconds: float = 5.0,
) -> bool:
    """Export PDF via weasyprint or Playwright Chromium (optional, fail-safe)."""
    out_file = Path(output_path)
    if not out_file.is_absolute():
        out_file = _REPO_ROOT / out_file
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # 1. Try weasyprint
    try:
        from weasyprint import HTML  # type: ignore
        logger.info("Generating PDF with weasyprint...")
        HTML(string=html_content).write_pdf(str(out_file))
        logger.info(f"PDF successfully generated via weasyprint: {out_file}")
        return True
    except ImportError:
        logger.debug("weasyprint not installed, attempting Playwright Chromium fallback")
    except Exception as e:
        logger.warning(f"weasyprint PDF generation failed: {e}. Trying Playwright Chromium.")

    # 2. Try Playwright Chromium with strict timeout to prevent sandbox hangs
    try:
        import concurrent.futures

        logger.info(f"Attempting PDF generation via Playwright Chromium (timeout={timeout_seconds}s)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _generate_pdf_playwright, html_content, out_file, html_file_path
            )
            future.result(timeout=timeout_seconds)
            logger.info(f"PDF successfully generated via Playwright Chromium: {out_file}")
            return True
    except ImportError:
        logger.debug("Playwright not installed, skipping PDF generation")
    except TimeoutError:
        logger.warning("Playwright Chromium PDF generation timed out (sandbox/environment restriction). Skipping.")
    except Exception as e:
        logger.warning(f"Playwright Chromium PDF generation skipped or failed: {e}")

    logger.info("Optional PDF generation skipped (neither weasyprint nor Playwright Chromium available).")
    return False


def run_digest(
    region: str = "All",
    limit: int = 15,
    model: str = DEFAULT_MODEL,
    db_path: Optional[str] = None,
    motherduck: bool = False,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    template_path: str = DEFAULT_TEMPLATE_PATH,
    dry_run: bool = False,
    generate_pdf: bool = False,
    client: Optional[Any] = None,
) -> dict[str, Any]:
    """Orchestrate end-to-end scout digest pipeline."""
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = _REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    html_path = out_dir / "sunday_digest.html"
    whatsapp_path = out_dir / "whatsapp_digest.txt"
    pdf_path = out_dir / "sunday_digest.pdf"

    # Step 1: Query Gold Layer
    con = get_duckdb_connection(db_path=db_path, motherduck=motherduck, read_only=True)
    try:
        prospects = fetch_top_prospects(con=con, region=region, limit=limit)
    finally:
        con.close()

    # Step 2: Generate Claude AI Blurbs
    prospects = generate_scouting_blurbs(
        players=prospects,
        client=client,
        model=model,
        dry_run=dry_run,
    )

    # Step 3: Render Jinja2 RTL HTML
    html_content = render_html_digest(
        prospects=prospects,
        region=region,
        template_path=template_path,
        output_path=str(html_path),
        model_name=model if not dry_run else f"{model} (offline fallback)",
    )

    # Step 4: Export WhatsApp / Telegram Markdown
    whatsapp_text = export_whatsapp_digest(
        prospects=prospects,
        region=region,
        output_path=str(whatsapp_path),
    )

    # Step 5: Optional PDF Export
    pdf_generated = False
    if generate_pdf:
        pdf_generated = export_pdf_digest(
            html_content=html_content,
            output_path=str(pdf_path),
            html_file_path=str(html_path),
        )

    return {
        "region": region,
        "prospects_count": len(prospects),
        "html_path": str(html_path),
        "whatsapp_path": str(whatsapp_path),
        "pdf_path": str(pdf_path) if pdf_generated else None,
        "prospects": prospects,
    }


def main():
    """CLI entrypoint for scout digest generation."""
    parser = argparse.ArgumentParser(
        description="Israeli Football Scouting Digest: Top Breakout Prospects"
    )
    parser.add_argument(
        "--region",
        type=str,
        default="All",
        choices=["All", "North", "South", "Central", "all", "north", "south", "central"],
        help="Filter prospects by league region (All, North, South, Central)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Number of top prospects to evaluate (default: 15)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"Anthropic Claude model (default: {DEFAULT_MODEL}, e.g. claude-3-7-sonnet-20250219)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"Path to DuckDB warehouse file (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--motherduck",
        action="store_true",
        help="Connect to cloud MotherDuck database instead of local file",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for generated reports (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--template-path",
        type=str,
        default=DEFAULT_TEMPLATE_PATH,
        help=f"Path to Jinja2 HTML template (default: {DEFAULT_TEMPLATE_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate grounded fallback blurbs without invoking the Claude API",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        default=False,
        help="Attempt optional PDF generation via weasyprint or Chromium",
    )

    args = parser.parse_args()

    result = run_digest(
        region=args.region,
        limit=args.limit,
        model=args.model,
        db_path=args.db_path,
        motherduck=args.motherduck,
        output_dir=args.output_dir,
        template_path=args.template_path,
        dry_run=args.dry_run,
        generate_pdf=args.pdf,
    )

    print("\n" + "=" * 60)
    print("🏆 Scout Digest Generated Successfully!")
    print(f"📍 Region: {result['region']}")
    print(f"👥 Prospects Evaluated: {result['prospects_count']}")
    print(f"📄 HTML Report: {result['html_path']}")
    print(f"📱 WhatsApp Summary: {result['whatsapp_path']}")
    if result["pdf_path"]:
        print(f"📑 PDF Report: {result['pdf_path']}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
