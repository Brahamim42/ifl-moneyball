-- depends_on: {{ ref('dim_dates') }}
{{
    config(
        materialized='incremental',
        unique_key=['match_id', 'event_seq'],
        incremental_strategy='merge'
    )
}}

with raw_apps as (
    select
        a.match_id,
        a.player_id,
        a.player_name,
        a.shirt_number,
        a.team_side,
        a.team_name,
        coalesce(a.league_id, m.league_id) as league_id,
        m.match_date,
        m.date_key,
        case when a.team_side = 'home' then m.home_team_id else m.away_team_id end as team_id,
        a.sub_in_minute,
        a.sub_out_minute,
        a.yellow_card_minute,
        a.red_card_minute,
        a.goals_scored,
        a.goal_minutes
    from {{ ref('stg_appearances') }} a
    join {{ ref('stg_matches') }} m
        on a.match_id = m.match_id
    {% if is_incremental() %}
    where m.match_date >= (select coalesce(max(d.full_date), date '1970-01-01') from {{ this }} f join {{ ref('dim_dates') }} d on f.date_key = d.date_key)
    {% endif %}
),
goals as (
    select
        match_id,
        player_id,
        team_id,
        league_id,
        date_key,
        'goal' as event_type,
        cast(unnest(goal_minutes) as integer) as minute,
        cast(null as integer) as stoppage_minute,
        team_side,
        player_name,
        shirt_number,
        cast(null as bigint) as related_player_id,
        cast(null as varchar) as related_player_name,
        cast(null as integer) as related_shirt_number,
        'Goal scored by ' || player_name as description
    from raw_apps
    where goals_scored > 0 and len(goal_minutes) > 0
),
yellow_cards as (
    select
        match_id,
        player_id,
        team_id,
        league_id,
        date_key,
        'yellow_card' as event_type,
        cast(yellow_card_minute as integer) as minute,
        cast(null as integer) as stoppage_minute,
        team_side,
        player_name,
        shirt_number,
        cast(null as bigint) as related_player_id,
        cast(null as varchar) as related_player_name,
        cast(null as integer) as related_shirt_number,
        'Yellow card awarded to ' || player_name as description
    from raw_apps
    where yellow_card_minute is not null
),
red_cards as (
    select
        match_id,
        player_id,
        team_id,
        league_id,
        date_key,
        'red_card' as event_type,
        cast(red_card_minute as integer) as minute,
        cast(null as integer) as stoppage_minute,
        team_side,
        player_name,
        shirt_number,
        cast(null as bigint) as related_player_id,
        cast(null as varchar) as related_player_name,
        cast(null as integer) as related_shirt_number,
        'Red card awarded to ' || player_name as description
    from raw_apps
    where red_card_minute is not null
),
sub_outs as (
    select
        match_id,
        player_id,
        player_name,
        shirt_number,
        team_side,
        sub_out_minute as minute
    from raw_apps
    where sub_out_minute is not null
),
substitutions as (
    select
        i.match_id,
        i.player_id,
        i.team_id,
        i.league_id,
        i.date_key,
        'substitution' as event_type,
        cast(i.sub_in_minute as integer) as minute,
        cast(null as integer) as stoppage_minute,
        i.team_side,
        i.player_name,
        i.shirt_number,
        o.player_id as related_player_id,
        o.player_name as related_player_name,
        o.shirt_number as related_shirt_number,
        'Substitution: ' || i.player_name || coalesce(' for ' || o.player_name, '') as description
    from raw_apps i
    left join sub_outs o
        on i.match_id = o.match_id
        and i.team_side = o.team_side
        and i.sub_in_minute = o.minute
    where i.sub_in_minute is not null
    qualify row_number() over (partition by i.match_id, i.player_id order by o.player_id is not null desc) = 1
),
unioned as (
    select * from goals
    union all
    select * from yellow_cards
    union all
    select * from red_cards
    union all
    select * from substitutions
),
sequenced as (
    select
        match_id,
        row_number() over (partition by match_id order by minute, event_type, player_id) as event_seq,
        player_id,
        team_id,
        league_id,
        date_key,
        event_type,
        minute,
        stoppage_minute,
        team_side,
        player_name,
        shirt_number,
        related_player_id,
        related_player_name,
        related_shirt_number,
        description
    from unioned
)
select
    md5(cast(match_id as varchar) || '-' || cast(event_seq as varchar)) as event_key,
    cast(match_id as bigint) as match_id,
    cast(event_seq as integer) as event_seq,
    cast(player_id as bigint) as player_id,
    cast(team_id as bigint) as team_id,
    cast(league_id as bigint) as league_id,
    cast(date_key as integer) as date_key,
    cast(event_type as varchar) as event_type,
    cast(minute as integer) as minute,
    cast(stoppage_minute as integer) as stoppage_minute,
    cast(team_side as varchar) as team_side,
    cast(player_name as varchar) as player_name,
    cast(shirt_number as integer) as shirt_number,
    cast(related_player_id as bigint) as related_player_id,
    cast(related_player_name as varchar) as related_player_name,
    cast(related_shirt_number as integer) as related_shirt_number,
    cast(description as varchar) as description,
    current_timestamp as ingested_at
from sequenced
