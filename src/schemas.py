"""Pydantic v2 data contracts and validation schemas for IFA data ingestion."""

from datetime import date, datetime
import re
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


def parse_hebrew_minute(v: Any) -> Optional[int]:
    """Parse Hebrew minute strings into integers.
    
    Handles:
    - 64 -> 64
    - '64' -> 64
    - 'דקה 64' -> 64
    - 'דקה64' -> 64
    - '64דק\'' -> 64
    - '64 דק\'' -> 64
    - 'יצאדקה83' -> 83
    - 'נכנסדקה46' -> 46
    - 'כרטיס צהובדקה32' -> 32
    - 'כרטיס צהוב דקה 32' -> 32
    - None / '' -> None
    """
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        # Extract digits after דקה, or anywhere in the string
        # If there are multiple numbers (e.g. +3), find the primary minute
        minute_match = re.search(r'(?:דקה|דק\'?)\s*(\d+)', v)
        if minute_match:
            return int(minute_match.group(1))
        # Match standalone digits
        digits = re.search(r'\d+', v)
        if digits:
            return int(digits.group(0))
    raise ValueError(f"Cannot parse minute from value: {v!r}")


def parse_hebrew_date(v: Any) -> Optional[date]:
    """Parse date strings (including Hebrew portal formats) into datetime.date.
    
    Handles:
    - datetime.date / datetime.datetime
    - '12/05/2006' -> date(2006, 5, 12)
    - '05/2000' -> date(2000, 5, 1)
    - '2006-05-12' -> date(2006, 5, 12)
    - '18/09/2026 | 11:00' -> date(2026, 9, 18)
    - None / '' -> None
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        # Check DD/MM/YYYY
        d_match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', v)
        if d_match:
            day = int(d_match.group(1))
            month = int(d_match.group(2))
            year = int(d_match.group(3))
            return date(year, month, day)
        # Check YYYY-MM-DD
        iso_match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', v)
        if iso_match:
            year = int(iso_match.group(1))
            month = int(iso_match.group(2))
            day = int(iso_match.group(3))
            return date(year, month, day)
        # Check MM/YYYY (e.g. '05/2000' in player birth date)
        my_match = re.search(r'(\d{1,2})/(\d{4})', v)
        if my_match:
            month = int(my_match.group(1))
            year = int(my_match.group(2))
            return date(year, month, 1)
    raise ValueError(f"Cannot parse date from value: {v!r}")


def parse_shirt_number(v: Any) -> Optional[int]:
    """Parse shirt numbers like 'מס\' 11' into integer 11."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        match = re.search(r'\d+', v)
        if match:
            return int(match.group(0))
    return None


class BaseContract(BaseModel):
    """Base model enforcing strict types, validation and whitespace trimming."""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
        validate_assignment=True,
    )


class LeagueSeason(BaseContract):
    """Configuration and metadata contract for an IFA target division."""
    league_id: int = Field(..., description="Official IFA league identifier")
    season_id: int = Field(..., description="Official IFA season identifier")
    tier: str = Field(..., description="League competition tier (e.g. Senior Tier 3, U19 Tier 2)")
    region: Optional[str] = Field(None, description="Geographic region (North, South, Central)")
    name_en: str = Field(..., description="English league name")
    name_he: str = Field(..., description="Hebrew official IFA league name")


class MatchMetadata(BaseContract):
    """High-level match metadata extracted from IFA match page."""
    match_id: int = Field(..., description="Official IFA match ID")
    season_id: Optional[int] = Field(None, description="IFA season ID")
    league_id: Optional[int] = Field(None, description="IFA league ID")
    round_number: Optional[int] = Field(None, description="Matchweek / Round number")
    match_date: Optional[date] = Field(None, description="Scheduled match date")
    match_time: Optional[str] = Field(None, description="Scheduled kick-off time (HH:MM)")
    home_team_id: Optional[int] = Field(None, description="Home club IFA ID if present")
    home_team_name: str = Field(..., description="Home club name")
    away_team_id: Optional[int] = Field(None, description="Away club IFA ID if present")
    away_team_name: str = Field(..., description="Away club name")
    home_score: Optional[int] = Field(None, ge=0, description="Final home score")
    away_score: Optional[int] = Field(None, ge=0, description="Final away score")
    home_half_time_score: Optional[int] = Field(None, ge=0, description="Half-time home score")
    away_half_time_score: Optional[int] = Field(None, ge=0, description="Half-time away score")
    stadium: Optional[str] = Field(None, description="Pitch / Stadium name")
    referee: Optional[str] = Field(None, description="Lead match referee")

    @field_validator("match_date", mode="before")
    @classmethod
    def validate_date(cls, v: Any) -> Optional[date]:
        return parse_hebrew_date(v)


class PlayerAppearance(BaseContract):
    """Individual player matchday squad appearance contract.
    
    Grain: (match_id, player_id)
    """
    match_id: int = Field(..., description="Match ID reference")
    player_id: int = Field(..., description="IFA Player ID reference")
    team_side: str = Field(..., pattern="^(home|away)$", description="'home' or 'away'")
    team_name: Optional[str] = Field(None, description="Name of club player represented")
    player_name: str = Field(..., description="Full player name")
    shirt_number: Optional[int] = Field(None, ge=1, le=999, description="Squad shirt number")
    is_starter: bool = Field(..., description="True if included in starting XI")
    is_captain: bool = Field(False, description="True if team captain")
    is_goalkeeper: bool = Field(False, description="True if listed as goalkeeper")
    sub_in_minute: Optional[int] = Field(None, ge=0, le=130, description="Minute substituted in")
    sub_out_minute: Optional[int] = Field(None, ge=0, le=130, description="Minute substituted out")
    yellow_card_minute: Optional[int] = Field(None, ge=0, le=130, description="Minute awarded yellow card")
    red_card_minute: Optional[int] = Field(None, ge=0, le=130, description="Minute sent off")
    goals_scored: int = Field(0, ge=0, description="Total goals scored in this match")
    goal_minutes: list[int] = Field(default_factory=list, description="List of minutes goals were scored")
    minutes_played: Optional[int] = Field(None, ge=0, le=130, description="Computed match minutes played")
    season_id: Optional[int] = Field(None, description="IFA season ID")
    league_id: Optional[int] = Field(None, description="IFA league ID")

    @field_validator("shirt_number", mode="before")
    @classmethod
    def validate_shirt(cls, v: Any) -> Optional[int]:
        return parse_shirt_number(v)

    @field_validator("sub_in_minute", "sub_out_minute", "yellow_card_minute", "red_card_minute", mode="before")
    @classmethod
    def validate_minutes(cls, v: Any) -> Optional[int]:
        return parse_hebrew_minute(v)

    @field_validator("minutes_played", mode="before")
    @classmethod
    def validate_minutes_played(cls, v: Any) -> Optional[int]:
        return parse_hebrew_minute(v)


class MatchEvent(BaseContract):
    """Discrete match event contract.
    
    Grain: (match_id, event_seq)
    """
    match_id: int = Field(..., description="Match ID reference")
    event_seq: int = Field(..., ge=1, description="Chronological event sequence number within match")
    event_type: str = Field(
        ...,
        pattern="^(goal|own_goal|penalty|yellow_card|red_card|substitution|period)$",
        description="Event classification"
    )
    minute: int = Field(..., ge=0, le=130, description="Regulation or extra time minute")
    stoppage_minute: Optional[int] = Field(None, ge=0, description="Added stoppage time minute (e.g. +3)")
    team_side: Optional[str] = Field(None, pattern="^(home|away)$", description="'home' or 'away'")
    team_name: Optional[str] = Field(None, description="Name of club credited with event")
    player_id: Optional[int] = Field(None, description="Primary player ID involved")
    player_name: Optional[str] = Field(None, description="Primary player name")
    shirt_number: Optional[int] = Field(None, ge=1, le=999, description="Primary player shirt number")
    sub_in_player_id: Optional[int] = Field(None, description="Player substituted in (for substitution events)")
    sub_in_player_name: Optional[str] = Field(None, description="Player name substituted in")
    sub_in_shirt_number: Optional[int] = Field(None, ge=1, le=999, description="Shirt number substituted in")
    description: Optional[str] = Field(None, description="Human-readable event description")

    @field_validator("minute", mode="before")
    @classmethod
    def validate_minute(cls, v: Any) -> int:
        parsed = parse_hebrew_minute(v)
        if parsed is None:
            raise ValueError(f"Minute cannot be None for MatchEvent: {v!r}")
        return parsed

    @field_validator("shirt_number", "sub_in_shirt_number", mode="before")
    @classmethod
    def validate_shirt(cls, v: Any) -> Optional[int]:
        return parse_shirt_number(v)


class PlayerProfile(BaseContract):
    """Player demographic and current club profile contract."""
    player_id: int = Field(..., description="Official IFA player ID")
    full_name: str = Field(..., description="Full player name in Hebrew")
    birth_date: Optional[date] = Field(None, description="Date of birth")
    nationality: Optional[str] = Field("ישראל", description="Player nationality")
    current_team_id: Optional[int] = Field(None, description="Current registered team ID")
    current_team_name: Optional[str] = Field(None, description="Current registered team name")
    current_league_id: Optional[int] = Field(None, description="Current registered league ID")
    current_league_name: Optional[str] = Field(None, description="Current registered league name")
    season_id: Optional[int] = Field(None, description="Active IFA season ID")
    stats: Optional[dict[str, Any]] = Field(None, description="Season-to-date summary metrics")

    @field_validator("birth_date", mode="before")
    @classmethod
    def validate_date(cls, v: Any) -> Optional[date]:
        return parse_hebrew_date(v)


class PlayerSnapshot(BaseContract):
    """Timestamped snapshot of a player's profile and club registration.
    
    Target: data/bronze/players/bronze_players.parquet
    """
    scraped_at: datetime = Field(..., description="Timestamp of snapshot ingestion (UTC)")
    player_id: int = Field(..., description="Official IFA player ID")
    birth_date: Optional[date] = Field(None, description="Date of birth")
    current_team_id: Optional[int] = Field(None, description="Current registered team ID")
    current_league_id: Optional[int] = Field(None, description="Current registered league ID")
    full_name: Optional[str] = Field(None, description="Full player name in Hebrew")
    nationality: Optional[str] = Field("ישראל", description="Player nationality")
    current_team_name: Optional[str] = Field(None, description="Current registered team name")
    current_league_name: Optional[str] = Field(None, description="Current registered league name")

    @field_validator("birth_date", mode="before")
    @classmethod
    def validate_date(cls, v: Any) -> Optional[date]:
        return parse_hebrew_date(v)


# =============================================================================
# PyArrow Storage Schemas for Parquet Persistence
# =============================================================================

import pyarrow as pa

MATCHES_PYARROW_SCHEMA = pa.schema([
    ("match_id", pa.int64()),
    ("season_id", pa.int64()),
    ("league_id", pa.int64()),
    ("round_number", pa.int64()),
    ("match_date", pa.date32()),
    ("match_time", pa.string()),
    ("home_team_id", pa.int64()),
    ("home_team_name", pa.string()),
    ("away_team_id", pa.int64()),
    ("away_team_name", pa.string()),
    ("home_score", pa.int64()),
    ("away_score", pa.int64()),
    ("home_half_time_score", pa.int64()),
    ("away_half_time_score", pa.int64()),
    ("stadium", pa.string()),
    ("referee", pa.string()),
])

APPEARANCES_PYARROW_SCHEMA = pa.schema([
    ("match_id", pa.int64()),
    ("player_id", pa.int64()),
    ("team_side", pa.string()),
    ("team_name", pa.string()),
    ("player_name", pa.string()),
    ("shirt_number", pa.int64()),
    ("is_starter", pa.bool_()),
    ("is_captain", pa.bool_()),
    ("is_goalkeeper", pa.bool_()),
    ("sub_in_minute", pa.int64()),
    ("sub_out_minute", pa.int64()),
    ("yellow_card_minute", pa.int64()),
    ("red_card_minute", pa.int64()),
    ("goals_scored", pa.int64()),
    ("goal_minutes", pa.list_(pa.int64())),
    ("minutes_played", pa.int64()),
    ("season_id", pa.int64()),
    ("league_id", pa.int64()),
])

PLAYERS_SNAPSHOT_PYARROW_SCHEMA = pa.schema([
    ("scraped_at", pa.timestamp("us", tz="UTC")),
    ("player_id", pa.int64()),
    ("birth_date", pa.date32()),
    ("current_team_id", pa.int64()),
    ("current_league_id", pa.int64()),
    ("full_name", pa.string()),
    ("nationality", pa.string()),
    ("current_team_name", pa.string()),
    ("current_league_name", pa.string()),
])


