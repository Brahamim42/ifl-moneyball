-- depends_on: {{ ref('dim_dates') }}
{{
    config(
        materialized='incremental',
        unique_key=['match_id', 'player_id'],
        incremental_strategy='merge'
    )
}}

with appearances as (
    select * from {{ ref('stg_appearances') }}
),
matches as (
    select * from {{ ref('stg_matches') }}
),
players_scd as (
    select * from {{ ref('snap_players_scd2') }}
),
calculated as (
    select
        a.match_id,
        a.player_id,
        m.match_date,
        m.date_key,
        coalesce(a.season_id, m.season_id, 28) as season_id,
        coalesce(a.league_id, m.league_id) as league_id,
        case when a.team_side = 'home' then m.home_team_id else m.away_team_id end as team_id,
        case when a.team_side = 'home' then m.away_team_id else m.home_team_id end as opponent_team_id,
        a.team_side,
        a.shirt_number,
        a.is_starter,
        a.is_captain,
        a.is_goalkeeper,
        a.sub_in_minute,
        a.sub_out_minute,
        a.yellow_card_minute,
        a.red_card_minute,
        case
            when not a.is_starter and a.sub_in_minute is null then 0
            else greatest(0,
                coalesce(least(a.sub_out_minute, a.red_card_minute), a.sub_out_minute, a.red_card_minute, 90)
                - case when a.is_starter then 0 else a.sub_in_minute end
            )
        end as minutes_played,
        coalesce(a.goals_scored, 0) as goals_scored,
        case when a.yellow_card_minute is not null then 1 else 0 end as yellow_cards,
        case when a.red_card_minute is not null then 1 else 0 end as red_cards,
        case 
            when a.team_side = 'home' and m.away_score = 0 then true
            when a.team_side = 'away' and m.home_score = 0 then true
            else false 
        end as is_clean_sheet
    from appearances a
    join matches m
        on a.match_id = m.match_id
    left join players_scd scd
        on a.player_id = scd.player_id
        and (
            (
                m.match_date >= cast(scd.dbt_valid_from as date)
                or scd.dbt_valid_from = (
                    select min(s2.dbt_valid_from) 
                    from players_scd s2 
                    where s2.player_id = scd.player_id
                )
            )
            and (m.match_date < cast(scd.dbt_valid_to as date) or scd.dbt_valid_to is null)
        )
    {% if is_incremental() %}
    where m.match_date >= (select coalesce(max(d.full_date), date '1970-01-01') from {{ this }} f join {{ ref('dim_dates') }} d on f.date_key = d.date_key)
    {% endif %}
)
select
    md5(cast(match_id as varchar) || '-' || cast(player_id as varchar)) as appearance_key,
    cast(match_id as bigint) as match_id,
    cast(player_id as bigint) as player_id,
    cast(team_id as bigint) as team_id,
    cast(opponent_team_id as bigint) as opponent_team_id,
    cast(league_id as bigint) as league_id,
    cast(season_id as bigint) as season_id,
    cast(date_key as integer) as date_key,
    cast(team_side as varchar) as team_side,
    cast(shirt_number as integer) as shirt_number,
    cast(is_starter as boolean) as is_starter,
    cast(is_captain as boolean) as is_captain,
    cast(is_goalkeeper as boolean) as is_goalkeeper,
    cast(sub_in_minute as integer) as sub_in_minute,
    cast(sub_out_minute as integer) as sub_out_minute,
    cast(yellow_card_minute as integer) as yellow_card_minute,
    cast(red_card_minute as integer) as red_card_minute,
    cast(minutes_played as integer) as minutes_played,
    cast(goals_scored as integer) as goals_scored,
    cast(yellow_cards as integer) as yellow_cards,
    cast(red_cards as integer) as red_cards,
    cast(is_clean_sheet as boolean) as is_clean_sheet,
    current_timestamp as ingested_at
from calculated
qualify row_number() over (partition by match_id, player_id order by is_starter desc) = 1
