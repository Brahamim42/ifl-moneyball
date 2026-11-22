-- Singular test: Verify match minutes are strictly between 0 and 120
select
    match_id,
    player_id,
    minutes_played
from {{ ref('fact_match_appearances') }}
where minutes_played < 0 or minutes_played > 120
