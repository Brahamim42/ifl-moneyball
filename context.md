# Project Context: Israeli Lower-League & Youth Football Scouting Pipeline (`il-scout-engine`)

> **Instructions for AI Coding Agents (Antigravity / Claude):**
> Read this file before executing any task in this repository. All code, schemas, file paths, and SQL models must adhere to the architecture, naming conventions, and scope defined below. Do not build components from future stages unless explicitly requested in the current prompt.

---

## 1. Project Overview

`il-scout-engine` is an end-to-end, production-grade **Data Engineering & Data Warehousing** pipeline (and solo micro-SaaS scouting product) that tracks undervalued U21/U23 breakout football prospects across Israel's lower senior and top youth divisions.

* **Primary Data Source:** The Israel Football Association portal ([football.org.il](https://www.football.org.il/)).
* **Core Value Proposition:** While Israel's top two tiers (Ligat Ha'Al and Liga Leumit) use expensive commercial scouting platforms, lower senior leagues (Tiers 3–5) and U19 youth leagues have zero automated analytics. Because all 10 target leagues share standardized IFA URL patterns (`football.org.il/leagues/league/?league_id=<ID>&season_id=<SEASON>`), a single parameterized ingestion and warehousing pipeline covers the entire ecosystem.
* **Portfolio & Engineering Goal:** Demonstrate senior-level data engineering and warehousing practices—decoupled cloud lakehouse storage, Kimball Star Schema modeling, SCD Type 2 transfer history, idempotent `MERGE` loads, automated `dbt` data quality testing, lineage documentation, and pipeline observability.

---

## 2. Target Scope: The 10 Israeli Leagues

All ingestion and transformation pipelines must be parameterized via `config/leagues.yaml` covering these 10 leagues:

| # | League Name (English) | Official IFA Name (Hebrew) | Tier / Category | Region |
| :--- | :--- | :--- | :--- | :--- |
| **1** | Liga Alef North | ליגה א' צפון | Senior Tier 3 | North |
| **2** | Liga Alef South | ליגה א' דרום | Senior Tier 3 | South |
| **3** | Liga Bet North A | ליגה ב' צפון א' | Senior Tier 4 | North |
| **4** | Liga Bet North B | ליגה ב' צפון ב' | Senior Tier 4 | North |
| **5** | Liga Bet South A | ליגה ב' דרום א' | Senior Tier 4 | South |
| **6** | Liga Bet South B | ליגה ב' דרום ב' | Senior Tier 4 | South |
| **7** | Liga Leumit LaNoar North | ליגה לאומית לנוער צפון | U19 Tier 2 | North |
| **8** | Liga Leumit LaNoar South | ליגה לאומית לנוער דרום | U19 Tier 2 | South |
| **9** | Liga Gimel Central | ליגה ג' מרכז | Senior Tier 5 | Central |
| **10** | Liga Gimel South | ליגה ג' דרום | Senior Tier 5 | South |

---

## 3. Target Architecture (Zero-Cost Cloud Lakehouse Stack)

### Layer 1: Ingestion & Decoupled Storage (Bronze)
* **Tools:** Python 3.11+, `asyncio`, `Playwright` (async), `selectolax` / `beautifulsoup4`, `Pydantic` v2, `tenacity`, `pyarrow` / `duckdb`.
* **Storage Format:** Immutable **Hive-partitioned Parquet** (`season_id=<ID>/league_id=<ID>/matches.parquet` and `appearances.parquet`) stored locally in `data/bronze/` and synced to **Cloudflare R2 / AWS S3** (via `httpfs` / `boto3`).
* **Incremental Watermarking:** Skip `match_id`s already present in Bronze unless `--lookback-days N` is passed (to catch late IFA stat corrections).
* **Player Bio Cache:** `data/bronze/players/bronze_players.parquet` stores timestamped player snapshots (`scraped_at`, `player_id`, `birth_date`, `current_team_id`, `current_league_id`) fetched only for unseen players or players whose club changed.
* **Observability:** Every ingestion run logs `run_id`, `started_at`, `finished_at`, `matches_scraped`, `players_enriched`, `rows_written`, `status`, and `error_message` to `data/bronze/etl_run_logs.parquet`.

### Layer 2: Data Warehouse & Dimensional Modeling (Silver)
* **Tools:** **DuckDB** (dev) / **MotherDuck** (prod) orchestrated by **`dbt-duckdb`**.
* **Kimball Star Schema:**
  * **Facts:**
    * `fact_match_appearances` — Grain: 1 row per `(match_id, player_id)`. Incremental `merge` strategy (`unique_key=['match_id', 'player_id']`). Computes exact `minutes_played` from `is_starter`, `sub_in_minute`, `sub_out_minute`, and `red_card_minute`.
    * `fact_match_events` — Grain: 1 row per `(match_id, event_seq)` (goals, penalties, yellow/red cards, substitutions).
  * **Dimensions:**
    * `dim_players` & `snap_players_scd2` — **SCD Type 2** via `dbt snapshot` (`strategy='check'`, `check_cols=['current_team_id', 'current_league_id']`) generating `dbt_valid_from`, `dbt_valid_to`, and `is_current` so historical goals always map to the club a player represented on that matchday.
    * `dim_teams` — Standardized via `macros/normalize_hebrew_club_name.sql` (normalizing prefixes like `מ.ס.`, `הפועל`, `מכבי`, `עירוני`, `בית"ר` and stripping sponsor suffixes).
    * `dim_leagues` & `dim_dates`.

### Layer 3: Analytical Marts & Data Quality (Gold)
* **Gold Marts (`dbt`):**
  * `mart_player_season_stats` — Season-to-date aggregates per `(player_id, season_id, league_id)`.
  * `mart_breakout_prospects` — Window-function metrics (`ROWS BETWEEN 4 PRECEDING AND CURRENT ROW`) for players with `>= 270` minutes, computing `npg_per_90`, `team_goal_share_pct`, league `z_score`, `is_u21_ironman`, `is_sudden_role_breakout`, `is_youth_senior_callup`, and a `0–100` `breakout_index`.
* **Testing & Lineage:** Full `schema.yml` constraints (`unique`, `not_null`, `relationships`, `accepted_values`), singular SQL tests (`18 <= squad_rows_per_match <= 32`, `0 <= minutes_played <= 120`), and `dbt docs generate` for the DAG lineage site.

### Layer 4: Intelligence, UI & Orchestration
* **AI Scouting Analyst (`src/reporting/scout_digest.py`):** Queries top 15 players from `mart_breakout_prospects`, generates strictly grounded 2-sentence Hebrew scouting notes via the **Anthropic Claude API** (zero hallucinated traits), and renders an RTL HTML/PDF digest (`dir="rtl"`) + WhatsApp text summary.
* **Interactive Explorer (`app.py`):** 3-tab **Streamlit** app deployed to Streamlit Community Cloud: (1) Breakout Radar, (2) Player Deep-Dive & SCD2 Transfer Timeline, and (3) Pipeline & Warehouse Health (`etl_run_logs` + `dbt` test status).
* **CI/CD (`.github/workflows/weekly_scout_sync.yml`):** Runs Saturday 23:30 IST & Sunday 07:00 IST (`ifa_crawler.py --lookback-days 7` → `player_enricher.py` → `dbt build` → gated `scout_digest.py`).

---

## 4. Repository Structure & Coding Standards

```text
il-scout-engine/
├── CONTEXT.md                  # This architecture & convention reference
├── pyproject.toml              # Managed with uv
├── config/
│   └── leagues.yaml            # 10 target IFA leagues
├── docs/
│   └── architecture.md         # Mermaid Star Schema ERD
├── src/
│   ├── schemas.py              # Pydantic v2 data contracts
│   ├── parsers/                # HTML/DOM parsers (selectolax / bs4)
│   ├── ingest/                 # Async Playwright crawler & player enricher
│   ├── reporting/              # Claude API digest & Jinja2 RTL templates
│   └── utils/                  # Run logger & R2/S3 helpers
├── dbt_project/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   ├── macros/                 # Hebrew club normalization macros
│   ├── models/
│   │   ├── staging/            # stg_* views over Bronze Parquet
│   │   ├── silver/             # Star Schema dims & incremental merge facts
│   │   └── gold/               # Scouting marts & window-function models
│   ├── snapshots/              # snap_players_scd2.sql
│   └── tests/                  # Singular SQL data quality tests
├── tests/                      # Pytest unit & parser tests
├── app.py                      # 3-tab Streamlit Explorer UI
└── .github/workflows/          # Weekend cron & dbt docs GitHub Pages
```

### Non-Negotiable Engineering Rules
1. **Strict Type & Schema Enforcement:** All scraped data must pass `Pydantic` v2 validation before touching Parquet.
2. **Idempotency Everywhere:** Re-running ingestion or `dbt build` for the same matchweek must never duplicate rows.
3. **Hebrew & RTL Safety:** Always use UTF-8, normalize Hebrew club names in Silver, and set `dir="rtl"` in HTML/Streamlit components displaying Hebrew text.
4. **Zero-Hallucination AI Reporting:** Claude scouting blurbs must only cite metrics present in the queried Gold JSON payload.
5. **No Hardcoded Secrets:** All API keys (`ANTHROPIC_API_KEY`, `MOTHERDUCK_TOKEN`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`) must be read from environment variables or `st.secrets`.
