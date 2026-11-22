"""
⚽ IL Scout Engine - Israeli Lower-League & Youth Football Scouting Explorer
=============================================================================
A production-grade, RTL-Hebrew-friendly Streamlit application connected to
MotherDuck/DuckDB lakehouse warehouse.

Tabs:
1. 🎯 Breakout Radar (רדאר פרוספקטים פורצים)
2. 🔍 Player Deep-Dive (ניתוח שחקן מעמיק & היסטוריית העברות SCD2)
3. 🩺 Pipeline & Warehouse Health (בריאות מחסן הנתונים ו-ETL)
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# -----------------------------------------------------------------------------
# 1. PAGE CONFIGURATION & RTL CSS INJECTION
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="IL Scout Engine | רדאר סקאוטינג ליגות נמוכות ונוער",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom RTL & Modern Cyber-Scout Slate Theme CSS
RTL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700;800;900&family=Rubik:wght@400;500;600;700&display=swap');

/* Global Font & Direction */
html, body, [class*="css"], .stApp {
    font-family: 'Heebo', 'Rubik', sans-serif !important;
    direction: rtl;
    text-align: right;
}

/* RTL Headers & Text */
h1, h2, h3, h4, h5, h6, p, span, label {
    font-family: 'Heebo', sans-serif !important;
    text-align: right;
    direction: rtl;
}

/* Sidebar Styling & RTL */
[data-testid="stSidebar"] {
    background-color: #0b1120;
    border-left: 1px solid rgba(59, 130, 246, 0.2);
    direction: rtl;
    text-align: right;
}

[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
    text-align: right;
    direction: rtl;
}

[data-testid="stSidebar"] .stSelectbox, 
[data-testid="stSidebar"] .stMultiSelect, 
[data-testid="stSidebar"] .stSlider {
    direction: rtl;
    text-align: right;
}

/* Metric Cards */
[data-testid="stMetric"] {
    background: linear-gradient(135deg, rgba(30, 41, 59, 0.85) 0%, rgba(15, 23, 42, 0.95) 100%);
    border: 1px solid rgba(59, 130, 246, 0.3);
    border-radius: 12px;
    padding: 16px 20px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
    text-align: right !important;
    direction: rtl !important;
    transition: transform 0.2s ease, border-color 0.2s ease;
}

[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    border-color: rgba(59, 130, 246, 0.6);
}

[data-testid="stMetricLabel"] {
    justify-content: flex-start !important;
    font-size: 0.88rem !important;
    font-weight: 600 !important;
    color: #94a3b8 !important;
    direction: rtl !important;
    text-align: right !important;
}

[data-testid="stMetricValue"] {
    color: #38bdf8 !important;
    font-size: 1.85rem !important;
    font-weight: 800 !important;
    direction: rtl !important;
    text-align: right !important;
}

/* Tabs Styling */
.stTabs [data-baseweb="tab-list"] {
    direction: rtl;
    justify-content: flex-start;
    gap: 8px;
    border-bottom: 2px solid rgba(51, 65, 85, 0.6);
    margin-bottom: 24px;
}

.stTabs [data-baseweb="tab"] {
    font-family: 'Heebo', sans-serif !important;
    font-weight: 700 !important;
    font-size: 1.05rem !important;
    padding: 12px 24px !important;
    background-color: transparent !important;
    color: #94a3b8 !important;
    border-radius: 8px 8px 0 0 !important;
    border: 1px solid transparent !important;
    transition: all 0.2s ease;
}

.stTabs [aria-selected="true"] {
    background: linear-gradient(180deg, rgba(37, 99, 235, 0.15) 0%, rgba(30, 41, 59, 0.4) 100%) !important;
    color: #38bdf8 !important;
    border-bottom: 3px solid #2563eb !important;
}

/* Custom Alert / Banner Cards */
.hero-header {
    background: linear-gradient(135deg, rgba(30, 58, 138, 0.6) 0%, rgba(15, 23, 42, 0.85) 100%);
    border: 1px solid rgba(59, 130, 246, 0.4);
    border-radius: 16px;
    padding: 24px 28px;
    margin-bottom: 28px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
}

.badge-tag {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 9999px;
    font-size: 0.78rem;
    font-weight: 700;
    margin-left: 6px;
    margin-bottom: 4px;
}

.badge-ironman {
    background-color: rgba(16, 185, 129, 0.2);
    color: #34d399;
    border: 1px solid rgba(16, 185, 129, 0.4);
}

.badge-breakout {
    background-color: rgba(245, 158, 11, 0.2);
    color: #fbbf24;
    border: 1px solid rgba(245, 158, 11, 0.4);
}

.badge-callup {
    background-color: rgba(139, 92, 246, 0.2);
    color: #a78bfa;
    border: 1px solid rgba(139, 92, 246, 0.4);
}

.badge-active {
    background-color: rgba(34, 197, 94, 0.2);
    color: #4ade80;
    border: 1px solid rgba(34, 197, 94, 0.4);
}

.badge-history {
    background-color: rgba(148, 163, 184, 0.2);
    color: #cbd5e1;
    border: 1px solid rgba(148, 163, 184, 0.4);
}

/* Timeline Cards */
.timeline-card {
    background: rgba(30, 41, 59, 0.6);
    border-right: 4px solid #2563eb;
    border-radius: 0 10px 10px 0;
    padding: 14px 18px;
    margin-bottom: 12px;
    border-top: 1px solid rgba(51, 65, 85, 0.5);
    border-bottom: 1px solid rgba(51, 65, 85, 0.5);
    border-left: 1px solid rgba(51, 65, 85, 0.5);
}

/* Dataframe Containers */
[data-testid="stDataFrame"] {
    direction: ltr !important; /* Table data rendered LTR for clean numbers, headers set right */
}

/* Download Button */
.stDownloadButton button {
    background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%) !important;
    color: white !important;
    font-weight: 700 !important;
    border-radius: 8px !important;
    border: none !important;
    padding: 10px 20px !important;
}

.stDownloadButton button:hover {
    background: linear-gradient(135deg, #1d4ed8 0%, #1e40af 100%) !important;
    box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4) !important;
}
</style>
"""
st.markdown(RTL_CSS, unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 2. DATABASE CONNECTION & DATA CACHING
# -----------------------------------------------------------------------------
@st.cache_resource(ttl=300)
def get_db_connection() -> Tuple[duckdb.DuckDBPyConnection, str]:
    """Connect to MotherDuck (cloud prod) or DuckDB (local dev) with graceful fallback."""
    # Check for MotherDuck token in st.secrets or os.environ
    token = os.environ.get("MOTHERDUCK_TOKEN")
    if not token and hasattr(st, "secrets"):
        try:
            token = st.secrets.get("MOTHERDUCK_TOKEN")
            if not token and "motherduck" in st.secrets:
                token = st.secrets["motherduck"].get("token")
        except Exception:
            token = None

    if token:
        try:
            conn_str = f"md:il_scout_engine?motherduck_token={token}"
            con = duckdb.connect(conn_str, read_only=True)
            return con, "MotherDuck Cloud (md:il_scout_engine)"
        except Exception as e:
            st.warning(f"⚠️ שגיאה בהתחברות ל-MotherDuck ({e}), עובר למחסן נתונים מקומי...")

    # Fallback to local DuckDB file
    candidate_paths = [
        Path("data/warehouse.duckdb"),
        Path("data/silver/scout_warehouse.duckdb"),
        Path("../data/silver/scout_warehouse.duckdb"),
    ]

    for p in candidate_paths:
        if p.exists():
            con = duckdb.connect(str(p), read_only=True)
            return con, f"DuckDB מקומי ({p.as_posix()})"

    # If neither exists, create memory connection
    con = duckdb.connect(":memory:")
    return con, "DuckDB In-Memory (Empty Fallback)"


# -----------------------------------------------------------------------------
# 3. DATA LOADING HELPERS
# -----------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_prospects_data(_con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Load and join breakout prospects with dimensional attributes."""
    try:
        tables = [r[0] for r in _con.execute("SHOW TABLES").fetchall()]
        if "mart_breakout_prospects" not in tables:
            return pd.DataFrame()

        has_dim_leagues = "dim_leagues" in tables

        if has_dim_leagues:
            query = """
                SELECT 
                    p.*,
                    COALESCE(l.league_name_he, p.league_name_en) AS league_name_he,
                    COALESCE(l.region, 'Unknown') AS region
                FROM mart_breakout_prospects p
                LEFT JOIN dim_leagues l ON p.league_id = l.league_id
                ORDER BY p.breakout_index DESC, p.minutes_played DESC, p.goals DESC
            """
        else:
            query = "SELECT *, 'כל האזורים' AS region, league_name_en AS league_name_he FROM mart_breakout_prospects ORDER BY breakout_index DESC"

        df = _con.execute(query).df()
        if not df.empty and "birth_date" in df.columns:
            df["birth_year"] = pd.to_datetime(df["birth_date"]).dt.year
        return df
    except Exception as e:
        st.error(f"שגיאה בטעינת פרוספקטים: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_player_match_history(_con: duckdb.DuckDBPyConnection, player_id: int) -> pd.DataFrame:
    """Load match-by-match appearance history for a specific player."""
    try:
        tables = [r[0] for r in _con.execute("SHOW TABLES").fetchall()]
        if "fact_match_appearances" not in tables:
            return pd.DataFrame()

        query = """
            SELECT 
                f.match_id,
                f.player_id,
                f.date_key,
                d.full_date,
                t.team_name_normalized AS team_name,
                opp.team_name_normalized AS opponent_name,
                f.minutes_played,
                f.goals_scored,
                f.is_starter,
                f.sub_in_minute,
                f.sub_out_minute,
                f.yellow_cards,
                f.red_cards
            FROM fact_match_appearances f
            LEFT JOIN dim_dates d ON f.date_key = d.date_key
            LEFT JOIN dim_teams t ON f.team_id = t.team_id
            LEFT JOIN dim_teams opp ON f.opponent_team_id = opp.team_id
            WHERE f.player_id = ?
            ORDER BY d.full_date ASC, f.match_id ASC
        """
        return _con.execute(query, [player_id]).df()
    except Exception as e:
        st.error(f"שגיאה בטעינת היסטוריית משחקים: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_player_scd2_history(_con: duckdb.DuckDBPyConnection, player_id: int) -> pd.DataFrame:
    """Load SCD Type 2 club transfer history for a specific player."""
    try:
        tables = [r[0] for r in _con.execute("SHOW TABLES").fetchall()]
        if "snap_players_scd2" not in tables:
            return pd.DataFrame()

        query = """
            SELECT 
                player_id,
                full_name,
                current_team_id,
                COALESCE(current_team_name_normalized, current_team_name) AS team_name,
                current_league_name AS league_name,
                dbt_valid_from,
                dbt_valid_to,
                CASE WHEN dbt_valid_to IS NULL THEN TRUE ELSE FALSE END AS is_current
            FROM snap_players_scd2
            WHERE player_id = ?
            ORDER BY dbt_valid_from ASC
        """
        return _con.execute(query, [player_id]).df()
    except Exception as e:
        st.error(f"שגיאה בטעינת היסטוריית העברות SCD2: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_etl_run_logs() -> pd.DataFrame:
    """Load live ETL audit run logs from parquet file."""
    candidate_paths = [
        Path("data/bronze/etl_run_logs.parquet"),
        Path("../data/bronze/etl_run_logs.parquet"),
    ]
    for p in candidate_paths:
        if p.exists():
            try:
                df = pd.read_parquet(p)
                if not df.empty and "started_at" in df.columns:
                    df = df.sort_values(by="started_at", ascending=False)
                return df
            except Exception as e:
                st.error(f"שגיאה בקריאת קובץ הלוגים {p}: {e}")
    return pd.DataFrame()


@st.cache_data(ttl=60)
def load_dbt_test_status() -> Dict[str, Any]:
    """Parse dbt run_results.json and manifest to extract test execution status."""
    target_paths = [
        Path("dbt_project/target/run_results.json"),
        Path("target/run_results.json"),
    ]
    for p in target_paths:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results = data.get("results", [])
                tests = [r for r in results if r.get("unique_id", "").startswith("test.")]
                models = [r for r in results if r.get("unique_id", "").startswith("model.")]

                passed_tests = sum(1 for t in tests if t.get("status") in ["pass", "success"])
                failed_tests = sum(1 for t in tests if t.get("status") in ["fail", "error"])
                warned_tests = sum(1 for t in tests if t.get("status") == "warn")

                # Parse detailed test records
                test_details = []
                for t in tests:
                    uid = t.get("unique_id", "")
                    parts = uid.split(".")
                    test_raw_name = parts[-2] if len(parts) >= 2 else uid

                    # Extract model target
                    model_target = "מחסן נתונים"
                    if "dim_" in uid:
                        for token in uid.split("_"):
                            if token in ["leagues", "players", "teams", "dates"]:
                                model_target = f"dim_{token}"
                                break
                    elif "fact_" in uid:
                        for token in ["match_appearances", "match_events"]:
                            if token in uid:
                                model_target = f"fact_{token}"
                                break
                    elif "mart_" in uid:
                        for token in ["breakout_prospects", "player_season_stats"]:
                            if token in uid:
                                model_target = f"mart_{token}"
                                break

                    test_details.append({
                        "test_name": test_raw_name,
                        "model_tested": model_target,
                        "status": "PASS" if t.get("status") in ["pass", "success"] else t.get("status").upper(),
                        "duration_sec": round(t.get("execution_time", 0.0), 3),
                        "failures": t.get("failures", 0),
                    })

                return {
                    "total_tests": len(tests),
                    "passed": passed_tests,
                    "failed": failed_tests,
                    "warned": warned_tests,
                    "models_count": len(models),
                    "generated_at": data.get("metadata", {}).get("generated_at", ""),
                    "elapsed_time": round(data.get("elapsed_time", 0.0), 2),
                    "details": pd.DataFrame(test_details) if test_details else pd.DataFrame(),
                }
            except Exception as e:
                st.warning(f"שגיאה בפענוח run_results.json: {e}")
    return {}


# -----------------------------------------------------------------------------
# 4. INITIALIZE CONNECTION & DATA
# -----------------------------------------------------------------------------
con, connection_label = get_db_connection()
df_prospects_all = load_prospects_data(con)

# -----------------------------------------------------------------------------
# 5. SIDEBAR FILTERS
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ⚙️ מסנני סקאוטינג")
    st.caption(f"🔌 מחובר אל: **{connection_label}**")
    st.markdown("---")

    if not df_prospects_all.empty:
        # League Filter
        leagues_list = sorted([
            l for l in df_prospects_all["league_name_he"].dropna().unique().tolist() if l
        ])
        selected_leagues = st.multiselect(
            "🏆 ליגה (League)",
            options=leagues_list,
            default=[],
            placeholder="כל הליגות (ברירת מחדל)",
        )

        # Tier Filter
        tiers_list = sorted([
            t for t in df_prospects_all["tier"].dropna().unique().tolist() if t
        ])
        selected_tiers = st.multiselect(
            "📊 דרג / קטגוריה (Tier)",
            options=tiers_list,
            default=[],
            placeholder="כל הדרגים (Senior / U19)",
        )

        # Region Filter (North / South / All)
        regions_list = ["הכל (כל האזורים)"] + sorted([
            r for r in df_prospects_all["region"].dropna().unique().tolist() if r and r != "Unknown"
        ])
        selected_region = st.selectbox(
            "🗺️ מחוז גיאוגרפי (Region)",
            options=regions_list,
            index=0,
        )

        # Birth Year Range Filter
        min_year_avail = int(df_prospects_all["birth_year"].dropna().min()) if "birth_year" in df_prospects_all.columns and not df_prospects_all["birth_year"].dropna().empty else 2000
        max_year_avail = int(df_prospects_all["birth_year"].dropna().max()) if "birth_year" in df_prospects_all.columns and not df_prospects_all["birth_year"].dropna().empty else 2008

        birth_years = st.slider(
            "🎂 טווח שנת לידה (Birth Year)",
            min_value=min_year_avail,
            max_value=max_year_avail,
            value=(min_year_avail, max_year_avail),
            step=1,
        )

        # Minimum Minutes Played Filter
        max_mins_avail = int(df_prospects_all["minutes_played"].max()) if not df_prospects_all.empty else 900
        slider_max = max(max_mins_avail, 90)
        min_minutes = st.slider(
            "⏱️ מינימום דקות משחק (Min Minutes)",
            min_value=0,
            max_value=slider_max,
            value=0,
            step=10,
        )

        # Club Filter
        clubs_list = sorted([
            c for c in df_prospects_all["team_name"].dropna().unique().tolist() if c
        ])
        selected_clubs = st.multiselect(
            "🛡️ מועדון (Club)",
            options=clubs_list,
            default=[],
            placeholder="כל המועדונים",
        )

        st.markdown("---")
        # Quick preset toggles
        st.markdown("##### ⚡ פילטרים מהירים:")
        u21_only = st.checkbox("רק פרוספקטים צעירים (U21)", value=False)
        ironman_only = st.checkbox("רק שחקני איירונמן (U21 Ironman)", value=False)
        role_breakout_only = st.checkbox("רק פריצת תפקיד / סופר-סאב", value=False)

    else:
        st.info("ממתין לטעינת נתונים ממחסן הנתונים...")
        selected_leagues, selected_tiers, selected_region = [], [], "הכל (כל האזורים)"
        birth_years = (2000, 2008)
        min_minutes = 0
        selected_clubs = []
        u21_only, ironman_only, role_breakout_only = False, False, False

    st.markdown("---")
    st.markdown(
        """
        <div style="font-size: 0.8rem; color: #64748b; line-height: 1.4;">
        ⚽ <b>IL Scout Engine v1.0</b><br/>
        מנוע בינת סקאוטינג לכדורגל ישראלי<br/>
        Kimball Star Schema + SCD2 + dbt
        </div>
        """,
        unsafe_allow_html=True,
    )

# -----------------------------------------------------------------------------
# 6. APPLY FILTERING LOGIC
# -----------------------------------------------------------------------------
df_filtered = df_prospects_all.copy()

if not df_filtered.empty:
    if selected_leagues:
        df_filtered = df_filtered[df_filtered["league_name_he"].isin(selected_leagues)]

    if selected_tiers:
        df_filtered = df_filtered[df_filtered["tier"].isin(selected_tiers)]

    if selected_region != "הכל (כל האזורים)":
        df_filtered = df_filtered[df_filtered["region"] == selected_region]

    if "birth_year" in df_filtered.columns:
        df_filtered = df_filtered[
            (df_filtered["birth_year"].isna()) |
            ((df_filtered["birth_year"] >= birth_years[0]) & (df_filtered["birth_year"] <= birth_years[1]))
        ]

    df_filtered = df_filtered[df_filtered["minutes_played"] >= min_minutes]

    if selected_clubs:
        df_filtered = df_filtered[df_filtered["team_name"].isin(selected_clubs)]

    if u21_only and "is_u21" in df_filtered.columns:
        df_filtered = df_filtered[df_filtered["is_u21"] == True]

    if ironman_only and "is_u21_ironman" in df_filtered.columns:
        df_filtered = df_filtered[df_filtered["is_u21_ironman"] == True]

    if role_breakout_only and "is_sudden_role_breakout" in df_filtered.columns:
        df_filtered = df_filtered[df_filtered["is_sudden_role_breakout"] == True]


# -----------------------------------------------------------------------------
# 7. APP HEADER
# -----------------------------------------------------------------------------
st.markdown(
    """
    <div class="hero-header">
        <h1 style="margin: 0; color: #f8fafc; font-size: 2.2rem; font-weight: 800;">
            ⚽ IL Scout Engine | מנוע סקאוטינג וגילוי כישרונות
        </h1>
        <p style="margin: 8px 0 0 0; color: #94a3b8; font-size: 1.05rem;">
            ניתוח פרוספקטים מתקדמים, זיהוי פריצות U21/U23 ומעקב העברות SCD2 בליגות הנמוכות ונוער בישראל
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# -----------------------------------------------------------------------------
# 8. TABS INITIALIZATION
# -----------------------------------------------------------------------------
tab1, tab2, tab3 = st.tabs([
    "🎯 רדאר פריצה (Breakout Radar)",
    "🔍 ניתוח שחקן מעמיק (Player Deep-Dive & SCD2)",
    "🩺 בריאות מחסן וצינורות (Pipeline & Warehouse Health)",
])


# =============================================================================
# TAB 1: BREAKOUT RADAR
# =============================================================================
with tab1:
    st.markdown("### 🎯 רדאר כישרונות פורצים (Breakout Radar)")
    st.caption("פרוספקטים צעירים מדורגים על בסיס מדד פריצה משוקלל (0-100), נתח שערים קבוצתי ודקות משחק.")

    if df_filtered.empty:
        st.warning("⚠️ לא נמצאו שחקנים התואמים את הגדרות הסינון שנבחרו בסרגל הצד. נסה להרחיב את המסננים.")
    else:
        # Top KPI Metric Cards
        total_prospects = len(df_filtered)
        top_prospect = df_filtered.iloc[0]["player_name"] if not df_filtered.empty else "N/A"
        top_score = df_filtered.iloc[0]["breakout_index"] if not df_filtered.empty else 0.0
        avg_age = round(df_filtered["age"].dropna().mean(), 1) if not df_filtered["age"].dropna().empty else 0.0
        total_goals = int(df_filtered["goals"].sum())
        max_goal_share = round(df_filtered["team_goal_share_pct"].max(), 1) if not df_filtered.empty else 0.0
        u21_ironmen_count = int(df_filtered["is_u21_ironman"].sum()) if "is_u21_ironman" in df_filtered.columns else 0

        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("פרוספקטים שעמדו בסינון", f"{total_prospects:,}")
        with col2:
            st.metric("מוביל מדד הפריצה", f"{top_prospect}", delta=f"{top_score:.1f} נק'")
        with col3:
            st.metric("גיל ממוצע", f"{avg_age}")
        with col4:
            st.metric("סך שערי פרוספקטים", f"{total_goals}")
        with col5:
            st.metric("נתח שערים מירבי", f"{max_goal_share}%", delta=f"{u21_ironmen_count} איירונמן")

        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)

        # Prepare Display Dataframe
        display_df = df_filtered.copy()

        # Format badge tags into a single column
        def make_badges_str(row):
            badges = []
            if row.get("is_u21_ironman"):
                badges.append("🛡️ איירונמן")
            if row.get("is_sudden_role_breakout"):
                badges.append("⚡ פריצת תפקיד")
            if row.get("is_youth_senior_callup"):
                badges.append("🚀 קריאה מנוער")
            return " ".join(badges) if badges else "—"

        display_df["badges_str"] = display_df.apply(make_badges_str, axis=1)

        # Columns to show
        show_cols = [
            "player_name",
            "team_name",
            "league_name_he",
            "age",
            "minutes_played",
            "goals",
            "npg_per_90",
            "team_goal_share_pct",
            "breakout_index",
            "badges_str",
        ]

        # Rename for clean Hebrew header display
        rename_map = {
            "player_name": "שם שחקן",
            "team_name": "קבוצה",
            "league_name_he": "ליגה",
            "age": "גיל",
            "minutes_played": "דקות",
            "goals": "שערים",
            "npg_per_90": "שערים ל-90",
            "team_goal_share_pct": "נתח שערים (%)",
            "breakout_index": "מדד פריצה",
            "badges_str": "תגי איתור",
        }

        table_data = display_df[show_cols].rename(columns=rename_map)

        # Configure columns with progress bars
        column_config = {
            "מדד פריצה": st.column_config.ProgressColumn(
                "מדד פריצה (0-100)",
                help="ציון משוקלל המשלב דקות, קצב הבקעה, נתח שערים ותגי פרוספקט",
                format="%.1f",
                min_value=0.0,
                max_value=100.0,
            ),
            "נתח שערים (%)": st.column_config.ProgressColumn(
                "נתח שערים קבוצתי (%)",
                help="אחוז השערים שהבקיע השחקן מתוך כלל שערי הקבוצה",
                format="%.1f%%",
                min_value=0.0,
                max_value=100.0,
            ),
            "שערים ל-90": st.column_config.NumberColumn(
                "שערים ל-90",
                format="%.2f",
            ),
            "דקות": st.column_config.NumberColumn(
                "דקות משחק",
                format="%d",
            ),
            "שערים": st.column_config.NumberColumn(
                "שערים",
                format="%d",
            ),
            "גיל": st.column_config.NumberColumn(
                "גיל",
                format="%d",
            ),
        }

        st.dataframe(
            table_data,
            column_config=column_config,
            use_container_width=True,
            hide_index=True,
            height=460,
        )

        # CSV Download Button with UTF-8 BOM for Hebrew Excel compatibility
        col_dl, col_space = st.columns([1, 3])
        with col_dl:
            csv_bytes = b"\xef\xbb\xbf" + table_data.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 הורד נתוני פרוספקטים (CSV)",
                data=csv_bytes,
                file_name=f"ifl_breakout_prospects_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True,
            )


# =============================================================================
# TAB 2: PLAYER DEEP-DIVE
# =============================================================================
with tab2:
    st.markdown("### 🔍 ניתוח שחקן מעמיק (Player Deep-Dive)")
    st.caption("פילוח ביצועים משחק-אחר-משחק, השוואת אחוזונים לרמת הליגה, והיסטוריית העברות SCD2.")

    if df_filtered.empty and df_prospects_all.empty:
        st.warning("⚠️ לא נטענו נתוני שחקנים ממחסן הנתונים.")
    else:
        # Use filtered prospects or fallback to all prospects for selector
        prospects_for_select = df_filtered if not df_filtered.empty else df_prospects_all

        # Build clean selector options
        player_options = {}
        for _, row in prospects_for_select.iterrows():
            pid = int(row["player_id"])
            pname = row["player_name"]
            pteam = row["team_name"]
            page = int(row["age"]) if pd.notna(row.get("age")) else 20
            pindex = round(float(row.get("breakout_index", 0.0)), 1)
            label = f"{pname} ({pteam}, גיל {page}) — מדד: {pindex}"
            player_options[label] = pid

        selected_label = st.selectbox(
            "👤 בחר שחקן לניתוח מעמיק:",
            options=list(player_options.keys()),
            index=0,
        )
        selected_player_id = player_options[selected_label]
        player_row = df_prospects_all[df_prospects_all["player_id"] == selected_player_id].iloc[0]

        # Player Profile Card
        badges_html = ""
        if player_row.get("is_u21_ironman"):
            badges_html += '<span class="badge-tag badge-ironman">🛡️ U21 איירונמן</span>'
        if player_row.get("is_sudden_role_breakout"):
            badges_html += '<span class="badge-tag badge-breakout">⚡ פריצת תפקיד</span>'
        if player_row.get("is_youth_senior_callup"):
            badges_html += '<span class="badge-tag badge-callup">🚀 קריאה מנוער</span>'

        st.markdown(
            f"""
            <div style="background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 12px; padding: 20px; margin-bottom: 24px;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
                    <div>
                        <h2 style="margin: 0; color: #f8fafc; font-size: 1.8rem;">{player_row['player_name']}</h2>
                        <div style="color: #94a3b8; font-size: 1rem; margin-top: 4px;">
                            קבוצה: <b style="color: #e2e8f0;">{player_row['team_name']}</b> | 
                            ליגה: <b style="color: #e2e8f0;">{player_row.get('league_name_he', player_row.get('league_name_en'))}</b> | 
                            גיל: <b style="color: #e2e8f0;">{player_row['age']}</b> (יליד {player_row.get('birth_date', 'N/A')})
                        </div>
                    </div>
                    <div style="margin-top: 8px;">
                        {badges_html}
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Player KPI Cards
        k1, k2, k3, k4, k5 = st.columns(5)
        with k1:
            st.metric("מדד פריצה משוקלל", f"{player_row.get('breakout_index', 0.0):.1f}")
        with k2:
            st.metric("דקות משחק", f"{int(player_row.get('minutes_played', 0))}")
        with k3:
            st.metric("שערים", f"{int(player_row.get('goals', 0))}")
        with k4:
            st.metric("שערים ל-90 דק'", f"{player_row.get('npg_per_90', 0.0):.2f}")
        with k5:
            st.metric("נתח שערים קבוצתי", f"{player_row.get('team_goal_share_pct', 0.0):.1f}%")

        st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True)

        # Row with 2 Columns: (A) Match-by-Match Chart, (B) Percentile Radar
        col_chart1, col_chart2 = st.columns([1.1, 0.9])

        with col_chart1:
            st.markdown("#### 📈 דקות ושערים משחק-אחר-משחק")
            df_matches = load_player_match_history(con, selected_player_id)

            if df_matches.empty:
                st.info("לא נמצאו נתוני משחקים מפורטים עבור שחקן זה במאגר.")
            else:
                # Build label for X axis (Date + Opponent)
                df_matches["match_label"] = df_matches.apply(
                    lambda r: f"{str(r['full_date'])[:10]} נגד {r['opponent_name'] or 'יריבה'}",
                    axis=1,
                )

                fig_matches = go.Figure()

                # Bar for minutes played
                fig_matches.add_trace(
                    go.Bar(
                        x=df_matches["match_label"],
                        y=df_matches["minutes_played"],
                        name="דקות משחק",
                        marker_color="#2563EB",
                        hovertemplate="<b>%{x}</b><br>דקות משחק: %{y}<extra></extra>",
                    )
                )

                # Line / Scatter or secondary bar for goals
                if df_matches["goals_scored"].sum() > 0:
                    fig_matches.add_trace(
                        go.Scatter(
                            x=df_matches["match_label"],
                            y=df_matches["goals_scored"],
                            name="שערים שהובקעו",
                            mode="lines+markers+text",
                            text=df_matches["goals_scored"].apply(lambda g: f"⚽ {g}" if g > 0 else ""),
                            textposition="top center",
                            yaxis="y2",
                            marker=dict(size=12, color="#F59E0B", symbol="star"),
                            line=dict(color="#F59E0B", width=2),
                            hovertemplate="שערים: %{y}<extra></extra>",
                        )
                    )

                # Update layout for dual axis & dark slate styling
                fig_matches.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(15, 23, 42, 0.6)",
                    font=dict(family="Heebo, Rubik, sans-serif", color="#F8FAFC"),
                    margin=dict(l=40, r=40, t=30, b=60),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="right",
                        x=1,
                    ),
                    xaxis=dict(
                        showgrid=False,
                        tickangle=-25,
                        tickfont=dict(size=10, color="#94A3B8"),
                    ),
                    yaxis=dict(
                        title=dict(text="דקות משחק", font=dict(color="#38BDF8", size=12)),
                        tickfont=dict(color="#94A3B8"),
                        range=[0, max(100, int(df_matches["minutes_played"].max() * 1.15))],
                        gridcolor="rgba(51, 65, 85, 0.4)",
                    ),
                    yaxis2=dict(
                        title=dict(text="שערים", font=dict(color="#F59E0B", size=12)),
                        tickfont=dict(color="#F59E0B"),
                        overlaying="y",
                        side="right",
                        range=[0, max(3, int(df_matches["goals_scored"].max() + 1))],
                        showgrid=False,
                        dtick=1,
                    ),
                )
                st.plotly_chart(fig_matches, use_container_width=True)

        with col_chart2:
            st.markdown("#### 🕸️ השוואת אחוזונים ביחס לליגה")

            # Calculate percentiles across all players in same league / dataset
            league_players = df_prospects_all[df_prospects_all["league_id"] == player_row["league_id"]]
            if league_players.empty or len(league_players) < 5:
                league_players = df_prospects_all

            def calc_percentile(col_name: str, val: float) -> float:
                series = league_players[col_name].dropna()
                if series.empty:
                    return 50.0
                return round((series <= val).mean() * 100.0, 1)

            p_mins = calc_percentile("minutes_played", player_row.get("minutes_played", 0))
            p_goals = calc_percentile("goals", player_row.get("goals", 0))
            p_npg = calc_percentile("npg_per_90", player_row.get("npg_per_90", 0.0))
            p_share = calc_percentile("team_goal_share_pct", player_row.get("team_goal_share_pct", 0.0))
            p_breakout = calc_percentile("breakout_index", player_row.get("breakout_index", 0.0))

            categories = ["דקות משחק", "שערים", "שערים ל-90", "נתח שערים %", "מדד פריצה"]
            percentiles = [p_mins, p_goals, p_npg, p_share, p_breakout]

            fig_radar = go.Figure()
            fig_radar.add_trace(
                go.Scatterpolar(
                    r=percentiles + [percentiles[0]],
                    theta=categories + [categories[0]],
                    fill="toself",
                    fillcolor="rgba(37, 99, 235, 0.35)",
                    line=dict(color="#38BDF8", width=2.5),
                    marker=dict(size=7, color="#60A5FA"),
                    hovertemplate="%{theta}: אחוזון %{r}%<extra></extra>",
                )
            )

            fig_radar.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="Heebo, Rubik, sans-serif", color="#F8FAFC"),
                margin=dict(l=30, r=30, t=30, b=30),
                polar=dict(
                    radialaxis=dict(
                        visible=True,
                        range=[0, 100],
                        tickvals=[25, 50, 75, 100],
                        tickfont=dict(size=9, color="#64748B"),
                        gridcolor="rgba(51, 65, 85, 0.5)",
                    ),
                    angularaxis=dict(
                        gridcolor="rgba(51, 65, 85, 0.5)",
                        tickfont=dict(size=11, color="#E2E8F0"),
                    ),
                    bgcolor="rgba(15, 23, 42, 0.6)",
                ),
            )
            st.plotly_chart(fig_radar, use_container_width=True)

            # League Percentile Bars
            st.markdown("##### 📊 מדדי אחוזון ביחס לליגה:")
            for cat, pct in zip(categories, percentiles):
                p_col1, p_col2 = st.columns([1, 2])
                with p_col1:
                    st.caption(f"**{cat}**")
                with p_col2:
                    st.progress(int(min(max(pct, 0.0), 100.0)), text=f"אחוזון {pct:.1f}%")

        st.markdown("---")

        # SCD Type 2 Club Transfer Timeline
        st.markdown("#### 🔄 ציר זמן היסטוריית מעברים ומועדונים (SCD Type 2 Timeline)")
        st.caption("מעקב אחר רישום שחקנים ושינויי מועדונים מתוך טבלת המעקב ההיסטורית `snap_players_scd2`.")

        df_scd2 = load_player_scd2_history(con, selected_player_id)

        if df_scd2.empty:
            st.info("לא נמצאה היסטוריית מעברים מרובת תקופות עבור שחקן זה (שחקן רשום במועדון יחיד).")
        else:
            for idx, r in df_scd2.iterrows():
                is_curr = r["is_current"]
                badge_class = "badge-active" if is_curr else "badge-history"
                status_text = "🟢 מועדון פעיל כיום" if is_curr else "⚪ מועדון קודם"
                v_from = str(r["dbt_valid_from"])[:10] if pd.notna(r["dbt_valid_from"]) else "תחילת עונה"
                v_to = str(r["dbt_valid_to"])[:10] if pd.notna(r["dbt_valid_to"]) else "בתוקף"

                st.markdown(
                    f"""
                    <div class="timeline-card">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <b style="font-size: 1.15rem; color: #f8fafc;">{r['team_name'] or 'מועדון לא ידוע'}</b>
                                <span style="color: #94a3b8; font-size: 0.9rem; margin-right: 12px;">ליגה: {r['league_name'] or '—'}</span>
                            </div>
                            <span class="badge-tag {badge_class}">{status_text}</span>
                        </div>
                        <div style="color: #64748b; font-size: 0.82rem; margin-top: 6px;">
                            תקופת תוקף רשומה במערכת: <b>{v_from}</b> עד <b>{v_to}</b>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# =============================================================================
# TAB 3: PIPELINE & WAREHOUSE HEALTH
# =============================================================================
with tab3:
    st.markdown("### 🩺 בריאות צינורות הנתונים ומחסן המידע (Pipeline & Warehouse Health)")
    st.caption("ניטור בזמן אמת של ריצות ה-ETL מתוך `etl_run_logs`, סנכרון נתונים וסטטוס בדיקות האיכות של dbt.")

    # Ingestion & Warehouse Metrics
    df_logs = load_etl_run_logs()
    dbt_info = load_dbt_test_status()

    # Get total counts from DuckDB warehouse
    try:
        tables_in_db = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        total_matches_ingested = con.execute("SELECT COUNT(DISTINCT match_id) FROM fact_match_appearances").fetchone()[0] if "fact_match_appearances" in tables_in_db else 0
        total_players_ingested = con.execute("SELECT COUNT(DISTINCT player_id) FROM dim_players").fetchone()[0] if "dim_players" in tables_in_db else 0
        total_events_ingested = con.execute("SELECT COUNT(*) FROM fact_match_events").fetchone()[0] if "fact_match_events" in tables_in_db else 0
    except Exception:
        total_matches_ingested, total_players_ingested, total_events_ingested = 0, 0, 0

    # Last sync timestamp
    last_sync_str = "לא ידוע"
    if not df_logs.empty and "finished_at" in df_logs.columns:
        last_sync = df_logs["finished_at"].dropna().iloc[0]
        last_sync_str = str(last_sync)[:19]

    # Metrics Row
    h1, h2, h3, h4 = st.columns(4)
    with h1:
        st.metric("משחקים שנקלטו במחסן", f"{total_matches_ingested:,}")
    with h2:
        st.metric("שחקנים מתועדים (dim_players)", f"{total_players_ingested:,}")
    with h3:
        st.metric("אירועי משחק (שערים/כרטיסים)", f"{total_events_ingested:,}")
    with h4:
        st.metric("מועד סנכרון אחרון", f"{last_sync_str}")

    st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True)

    # dbt Quality Tests Dashboard
    st.markdown("#### 🧪 בדיקות איכות נתונים של dbt (dbt Data Quality Tests)")

    if dbt_info:
        total_tests = dbt_info.get("total_tests", 0)
        passed_tests = dbt_info.get("passed", 0)
        failed_tests = dbt_info.get("failed", 0)
        warned_tests = dbt_info.get("warned", 0)
        elapsed_sec = dbt_info.get("elapsed_time", 0.0)

        dt1, dt2, dt3, dt4 = st.columns(4)
        with dt1:
            st.metric("סה\"כ בדיקות איכות", f"{total_tests}")
        with dt2:
            st.metric("בדיקות שעברו בהצלחה (Pass)", f"{passed_tests}", delta="100% תקין" if failed_tests == 0 else None)
        with dt3:
            st.metric("בדיקות שנכשלו (Fail)", f"{failed_tests}", delta_color="inverse")
        with dt4:
            st.metric("זמן הרצת בדיקות", f"{elapsed_sec}s")

        # Interactive Test Table
        df_tests = dbt_info.get("details", pd.DataFrame())
        if not df_tests.empty:
            test_rename = {
                "test_name": "שם הבדיקה",
                "model_tested": "מודל נבדק",
                "status": "סטטוס",
                "duration_sec": "משך ביצוע (שניות)",
                "failures": "מספר חריגות",
            }
            st.dataframe(
                df_tests.rename(columns=test_rename),
                use_container_width=True,
                hide_index=True,
                height=240,
            )
    else:
        st.info("קובץ תוצאות בדיקות dbt (`target/run_results.json`) טרם נוצר. הרץ `dbt test` לעדכון.")

    st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)

    # Live Audit Table from etl_run_logs
    st.markdown("#### 📜 יומן ריצות ETL חי (Live Audit Table: `etl_run_logs`)")

    if df_logs.empty:
        st.info("טרם נרשמו ריצות ETL בקובץ `data/bronze/etl_run_logs.parquet`.")
    else:
        # Prepare clean display table for run logs
        logs_display = df_logs.copy()
        for dt_col in ["started_at", "finished_at"]:
            if dt_col in logs_display.columns:
                logs_display[dt_col] = logs_display[dt_col].astype(str).str[:19]

        logs_rename = {
            "run_id": "מזהה ריצה (Run ID)",
            "started_at": "זמן התחלה",
            "finished_at": "זמן סיום",
            "matches_scraped": "משחקים שנסרקו",
            "players_enriched": "שחקנים שהועשרו",
            "rows_written": "שורות שנכתבו",
            "status": "סטטוס ריצה",
            "error_message": "הודעת שגיאה",
        }

        # Status styling helper
        logs_display["status"] = logs_display["status"].apply(
            lambda s: "🟢 SUCCESS" if s == "SUCCESS" else f"🔴 {s}"
        )

        st.dataframe(
            logs_display.rename(columns=logs_rename),
            use_container_width=True,
            hide_index=True,
            height=260,
        )

    st.markdown("---")

    # Live Warehouse Tables Inventory
    st.markdown("#### 🗄️ פירוט טבלאות מחסן הנתונים (Warehouse Tables Inventory)")
    try:
        tables_inventory = []
        for t in [
            "dim_dates",
            "dim_leagues",
            "dim_players",
            "dim_teams",
            "fact_match_appearances",
            "fact_match_events",
            "mart_player_season_stats",
            "mart_breakout_prospects",
            "snap_players_scd2",
        ]:
            if t in tables_in_db:
                cnt = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                layer = "Gold Mart" if t.startswith("mart_") else ("Silver Fact" if t.startswith("fact_") else ("SCD2 Snapshot" if t.startswith("snap_") else "Silver Dim"))
                tables_inventory.append({
                    "טבלה / תצוגה": t,
                    "שכבת ארכיטקטורה": layer,
                    "מספר שורות": cnt,
                    "סטטוס זמינות": "🟢 זמין",
                })
        if tables_inventory:
            st.dataframe(pd.DataFrame(tables_inventory), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"שגיאה בהצגת טבלאות המחסן: {e}")
