{{ config(materialized='table') }}

with matches_teams as (
    select
        home_team_id as team_id,
        home_team_name as team_name_raw,
        stadium as home_stadium
    from {{ ref('stg_matches') }}
    where home_team_id is not null
    union all
    select
        away_team_id as team_id,
        away_team_name as team_name_raw,
        null as home_stadium
    from {{ ref('stg_matches') }}
    where away_team_id is not null
),
aggregated_teams as (
    select
        team_id,
        first(team_name_raw) as team_name_raw,
        max(home_stadium) as home_stadium
    from matches_teams
    group by team_id
)
select
    cast(team_id as bigint) as team_id,
    cast(team_name_raw as varchar) as team_name_raw,
    cast({{ normalize_hebrew_club_name('team_name_raw') }} as varchar) as team_name_normalized,
    cast({{ extract_hebrew_club_prefix('team_name_raw') }} as varchar) as club_prefix,
    cast(trim(regexp_replace({{ normalize_hebrew_club_name('team_name_raw') }}, '^(מ\.ס\.|מ\.כ\.|הפועל|מכבי|עירוני|בית"ר)\s+', '')) as varchar) as city,
    cast(home_stadium as varchar) as home_stadium
from aggregated_teams
