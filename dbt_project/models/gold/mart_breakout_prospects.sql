{{ config(materialized='table') }}

with player_season_stats as (
    select * from {{ ref('mart_player_season_stats') }}
),
match_appearances_windowed as (
    select
        f.match_id,
        f.player_id,
        f.team_id,
        f.league_id,
        coalesce(f.season_id, l.season_id, 28) as season_id,
        f.date_key,
        f.minutes_played,
        f.goals_scored,
        f.is_starter,
        -- Rolling 5-match window: ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        avg(f.minutes_played) over (
            partition by f.player_id, f.league_id
            order by f.date_key, f.match_id
            rows between 4 preceding and current row
        ) as rolling_avg_minutes_5,
        sum(f.minutes_played) over (
            partition by f.player_id, f.league_id
            order by f.date_key, f.match_id
            rows between 4 preceding and current row
        ) as rolling_minutes_5,
        sum(f.goals_scored) over (
            partition by f.player_id, f.league_id
            order by f.date_key, f.match_id
            rows between 4 preceding and current row
        ) as rolling_goals_5,

        -- Last 3 matches avg minutes:
        avg(f.minutes_played) over (
            partition by f.player_id, f.league_id
            order by f.date_key, f.match_id
            rows between 2 preceding and current row
        ) as avg_mins_last_3,

        -- Prior 5 matches avg minutes:
        avg(f.minutes_played) over (
            partition by f.player_id, f.league_id
            order by f.date_key, f.match_id
            rows between 7 preceding and 3 preceding
        ) as avg_mins_prior_5,

        -- Bench scoring checks for consecutive goals off the bench:
        case when not f.is_starter and f.goals_scored > 0 then 1 else 0 end as goal_off_bench,
        lag(case when not f.is_starter and f.goals_scored > 0 then 1 else 0 end, 1) over (
            partition by f.player_id, f.league_id
            order by f.date_key, f.match_id
        ) as prev_goal_off_bench
    from {{ ref('fact_match_appearances') }} f
    left join {{ ref('dim_leagues') }} l
        on f.league_id = l.league_id
),
latest_rolling as (
    select
        player_id,
        league_id,
        season_id,
        rolling_avg_minutes_5,
        rolling_minutes_5,
        rolling_goals_5,
        case 
            when rolling_minutes_5 > 0 
            then round(cast(rolling_goals_5 as double) * 90.0 / rolling_minutes_5, 2)
            else 0.0 
        end as rolling_npg_per_90
    from match_appearances_windowed
    qualify row_number() over (
        partition by player_id, league_id, season_id 
        order by date_key desc, match_id desc
    ) = 1
),
role_breakout as (
    select
        player_id,
        league_id,
        season_id,
        case when max(case 
            when (avg_mins_prior_5 is not null and (avg_mins_last_3 - avg_mins_prior_5) >= 45)
                 or (goal_off_bench = 1 and prev_goal_off_bench = 1)
            then 1 else 0 
        end) = 1 then true else false end as is_sudden_role_breakout
    from match_appearances_windowed
    group by player_id, league_id, season_id
),
senior_league_minutes as (
    select
        f.team_id,
        f.league_id,
        coalesce(f.season_id, l.season_id, 28) as season_id,
        count(distinct f.match_id) * 90 as available_senior_minutes
    from {{ ref('fact_match_appearances') }} f
    join {{ ref('dim_leagues') }} l 
        on f.league_id = l.league_id
    where l.category = 'Senior' or l.tier like 'Senior%'
    group by f.team_id, f.league_id, coalesce(f.season_id, l.season_id, 28)
),
youth_senior_callup as (
    select
        s.player_id,
        case when count(distinct case 
                    when dl.category = 'Youth' 
                         or s.current_league_id in (102, 103) 
                         or lower(coalesce(s.current_league_name, '')) like '%נוער%' 
                         or lower(coalesce(s.current_league_name, '')) like '%noar%' 
                    then 1 end) > 0
                 and count(distinct case 
                    when (dl.category = 'Senior' and dl.tier in ('Senior Tier 3', 'Senior Tier 4')) 
                         or s.current_league_id in (61, 62, 71, 72, 73, 74) 
                         or lower(coalesce(s.current_league_name, '')) like '%א%' 
                         or lower(coalesce(s.current_league_name, '')) like '%ב%' 
                         or lower(coalesce(s.current_league_name, '')) like '%alef%' 
                         or lower(coalesce(s.current_league_name, '')) like '%bet%' 
                    then 1 end) > 0
        then true else false end as is_youth_senior_callup
    from {{ ref('snap_players_scd2') }} s
    left join {{ ref('dim_leagues') }} dl 
        on s.current_league_id = dl.league_id
    group by s.player_id
),
league_benchmarks as (
    select
        league_id,
        season_id,
        avg(npg_per_90) as league_avg_npg_per_90,
        stddev(npg_per_90) as league_stddev_npg_per_90,
        avg(team_goal_share_pct) as league_avg_goal_share_pct,
        stddev(team_goal_share_pct) as league_stddev_goal_share_pct
    from player_season_stats
    group by league_id, season_id
),
calculated_prospects as (
    select
        md5(cast(pss.player_id as varchar) || '-' || cast(pss.season_id as varchar) || '-' || cast(pss.league_id as varchar)) as prospect_key,
        cast(pss.player_id as bigint) as player_id,
        cast(pss.season_id as bigint) as season_id,
        cast(pss.league_id as bigint) as league_id,
        cast(pss.player_name as varchar) as player_name,
        cast(pss.team_id as bigint) as team_id,
        cast(pss.team_name as varchar) as team_name,
        cast(pss.league_name_en as varchar) as league_name_en,
        cast(pss.tier as varchar) as tier,
        cast(pss.category as varchar) as category,
        cast(pss.birth_date as date) as birth_date,
        cast(pss.age as integer) as age,
        coalesce(pss.is_u21, false) as is_u21,
        coalesce(pss.is_u23, false) as is_u23,
        cast(pss.appearances as integer) as appearances,
        cast(pss.starts as integer) as starts,
        cast(pss.sub_appearances as integer) as sub_appearances,
        cast(pss.minutes_played as integer) as minutes_played,
        cast(pss.goals as integer) as goals,
        cast(pss.npg_per_90 as double) as npg_per_90,
        cast(pss.total_team_goals as integer) as total_team_goals,
        cast(pss.team_goal_share_pct as double) as team_goal_share_pct,
        cast(coalesce(lr.rolling_avg_minutes_5, cast(pss.minutes_played as double)) as double) as rolling_avg_minutes_5,
        cast(coalesce(lr.rolling_minutes_5, pss.minutes_played) as integer) as rolling_minutes_5,
        cast(coalesce(lr.rolling_goals_5, pss.goals) as integer) as rolling_goals_5,
        cast(coalesce(lr.rolling_npg_per_90, pss.npg_per_90) as double) as rolling_npg_per_90,
        cast(case 
            when coalesce(lb.league_stddev_npg_per_90, 0) > 0 
            then round((pss.npg_per_90 - lb.league_avg_npg_per_90) / lb.league_stddev_npg_per_90, 2)
            else 0.0 
        end as double) as league_z_score,
        cast(case 
            when coalesce(lb.league_stddev_goal_share_pct, 0) > 0 
            then round((pss.team_goal_share_pct - lb.league_avg_goal_share_pct) / lb.league_stddev_goal_share_pct, 2)
            else 0.0 
        end as double) as goal_share_z_score,
        cast(case 
            when coalesce(pss.age, 99) <= 21
                 and coalesce(slm.available_senior_minutes, 0) > 0
                 and (cast(pss.minutes_played as double) / slm.available_senior_minutes) >= 0.80
            then true 
            else false 
        end as boolean) as is_u21_ironman,
        cast(coalesce(rb.is_sudden_role_breakout, false) as boolean) as is_sudden_role_breakout,
        cast(coalesce(ysc.is_youth_senior_callup, false) as boolean) as is_youth_senior_callup
    from player_season_stats pss
    left join latest_rolling lr
        on pss.player_id = lr.player_id
        and pss.league_id = lr.league_id
        and pss.season_id = lr.season_id
    left join role_breakout rb
        on pss.player_id = rb.player_id
        and pss.league_id = rb.league_id
        and pss.season_id = rb.season_id
    left join senior_league_minutes slm
        on pss.team_id = slm.team_id
        and pss.league_id = slm.league_id
        and pss.season_id = slm.season_id
    left join youth_senior_callup ysc
        on pss.player_id = ysc.player_id
    left join league_benchmarks lb
        on pss.league_id = lb.league_id
        and pss.season_id = lb.season_id
    where pss.minutes_played >= {{ var('min_minutes_played', 270) }}
)
select
    prospect_key,
    player_id,
    season_id,
    league_id,
    player_name,
    team_id,
    team_name,
    league_name_en,
    tier,
    category,
    birth_date,
    age,
    is_u21,
    is_u23,
    appearances,
    starts,
    sub_appearances,
    minutes_played,
    goals,
    npg_per_90,
    total_team_goals,
    team_goal_share_pct,
    rolling_avg_minutes_5,
    rolling_minutes_5,
    rolling_goals_5,
    rolling_npg_per_90,
    league_z_score,
    goal_share_z_score,
    is_u21_ironman,
    is_sudden_role_breakout,
    is_youth_senior_callup,
    cast(round(greatest(0.0, least(100.0,
        (case when is_u21_ironman then 25.0 else 0.0 end) +
        (case when is_sudden_role_breakout then 25.0 else 0.0 end) +
        (case when is_youth_senior_callup then 20.0 else 0.0 end) +
        greatest(0.0, least(15.0, (league_z_score + 1.0) * 5.0)) +
        greatest(0.0, least(15.0, team_goal_share_pct * 0.30))
    )), 1) as double) as breakout_index,
    current_timestamp as calculated_at
from calculated_prospects
