with raw_appearances as (
    select * from {{ source('bronze', 'appearances') }}
)
select
    cast(match_id as bigint) as match_id,
    cast(player_id as bigint) as player_id,
    cast(team_side as varchar) as team_side,
    cast(team_name as varchar) as team_name,
    {{ normalize_hebrew_club_name('team_name') }} as team_name_normalized,
    cast(player_name as varchar) as player_name,
    cast(shirt_number as integer) as shirt_number,
    coalesce(cast(is_starter as boolean), false) as is_starter,
    coalesce(cast(is_captain as boolean), false) as is_captain,
    coalesce(cast(is_goalkeeper as boolean), false) as is_goalkeeper,
    cast(sub_in_minute as integer) as sub_in_minute,
    cast(sub_out_minute as integer) as sub_out_minute,
    cast(yellow_card_minute as integer) as yellow_card_minute,
    cast(red_card_minute as integer) as red_card_minute,
    coalesce(cast(goals_scored as integer), 0) as goals_scored,
    goal_minutes,
    cast(minutes_played as integer) as bronze_minutes_played,
    cast(season_id as bigint) as season_id,
    cast(league_id as bigint) as league_id
from raw_appearances
where match_id is not null and player_id is not null
qualify row_number() over (partition by match_id, player_id order by is_starter desc) = 1
