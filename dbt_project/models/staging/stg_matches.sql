with raw_matches as (
    select * from {{ source('bronze', 'matches') }}
)
select
    cast(match_id as bigint) as match_id,
    cast(season_id as bigint) as season_id,
    cast(league_id as bigint) as league_id,
    cast(round_number as integer) as round_number,
    cast(match_date as date) as match_date,
    cast(strftime(cast(match_date as date), '%Y%m%d') as integer) as date_key,
    cast(match_time as varchar) as match_time,
    cast(home_team_id as bigint) as home_team_id,
    cast(home_team_name as varchar) as home_team_name,
    {{ normalize_hebrew_club_name('home_team_name') }} as home_team_name_normalized,
    cast(away_team_id as bigint) as away_team_id,
    cast(away_team_name as varchar) as away_team_name,
    {{ normalize_hebrew_club_name('away_team_name') }} as away_team_name_normalized,
    cast(home_score as integer) as home_score,
    cast(away_score as integer) as away_score,
    cast(home_half_time_score as integer) as home_half_time_score,
    cast(away_half_time_score as integer) as away_half_time_score,
    cast(stadium as varchar) as stadium,
    cast(referee as varchar) as referee
from raw_matches
where match_id is not null
qualify row_number() over (partition by match_id order by match_date desc) = 1
