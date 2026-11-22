{{ config(materialized='table') }}

with appearances as (
    select
        f.match_id,
        f.player_id,
        f.team_id,
        f.league_id,
        coalesce(f.season_id, l.season_id, 28) as season_id,
        f.date_key,
        f.minutes_played,
        f.goals_scored,
        f.yellow_cards,
        f.red_cards,
        f.is_starter,
        case when not f.is_starter and f.minutes_played > 0 then 1 else 0 end as is_sub
    from {{ ref('fact_match_appearances') }} f
    left join {{ ref('dim_leagues') }} l
        on f.league_id = l.league_id
),
team_goals as (
    select
        team_id,
        league_id,
        season_id,
        sum(goals_scored) as total_team_goals
    from appearances
    group by team_id, league_id, season_id
),
player_aggregates as (
    select
        a.player_id,
        a.season_id,
        a.league_id,
        arg_max(a.team_id, a.date_key) as team_id,
        count(distinct a.match_id) as appearances,
        sum(case when a.is_starter then 1 else 0 end) as starts,
        sum(a.is_sub) as sub_appearances,
        sum(a.minutes_played) as minutes_played,
        sum(a.goals_scored) as goals,
        case 
            when sum(a.minutes_played) > 0 
            then round(cast(sum(a.goals_scored) as double) * 90.0 / sum(a.minutes_played), 2)
            else 0.0 
        end as npg_per_90,
        sum(a.yellow_cards) as yellow_cards,
        sum(a.red_cards) as red_cards
    from appearances a
    group by a.player_id, a.season_id, a.league_id
)
select
    md5(cast(pa.player_id as varchar) || '-' || cast(pa.season_id as varchar) || '-' || cast(pa.league_id as varchar)) as player_season_league_key,
    cast(pa.player_id as bigint) as player_id,
    cast(pa.season_id as bigint) as season_id,
    cast(pa.league_id as bigint) as league_id,
    cast(dp.full_name as varchar) as player_name,
    cast(pa.team_id as bigint) as team_id,
    cast(dt.team_name_normalized as varchar) as team_name,
    cast(dl.league_name_en as varchar) as league_name_en,
    cast(dl.tier as varchar) as tier,
    cast(dl.category as varchar) as category,
    cast(dp.birth_date as date) as birth_date,
    cast(dp.age as integer) as age,
    coalesce(dp.is_u21, false) as is_u21,
    coalesce(dp.is_u23, false) as is_u23,
    cast(pa.appearances as integer) as appearances,
    cast(pa.starts as integer) as starts,
    cast(pa.sub_appearances as integer) as sub_appearances,
    cast(pa.minutes_played as integer) as minutes_played,
    cast(pa.goals as integer) as goals,
    cast(pa.npg_per_90 as double) as npg_per_90,
    cast(pa.yellow_cards as integer) as yellow_cards,
    cast(pa.red_cards as integer) as red_cards,
    cast(coalesce(tg.total_team_goals, 0) as integer) as total_team_goals,
    cast(case 
        when coalesce(tg.total_team_goals, 0) > 0 
        then round((cast(pa.goals as double) / tg.total_team_goals) * 100.0, 2)
        else 0.0 
    end as double) as team_goal_share_pct,
    cast(case 
        when coalesce(tg.total_team_goals, 0) > 0 
        then round(cast(pa.goals as double) / tg.total_team_goals, 4)
        else 0.0 
    end as double) as team_goal_share,
    current_timestamp as calculated_at
from player_aggregates pa
left join team_goals tg 
    on pa.team_id = tg.team_id 
    and pa.league_id = tg.league_id 
    and pa.season_id = tg.season_id
left join {{ ref('dim_players') }} dp 
    on pa.player_id = dp.player_id
left join {{ ref('dim_teams') }} dt 
    on pa.team_id = dt.team_id
left join {{ ref('dim_leagues') }} dl 
    on pa.league_id = dl.league_id
