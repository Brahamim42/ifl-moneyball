with raw_players as (
    select * from {{ source('bronze', 'players') }}
),
matches_teams as (
    select distinct home_team_id as team_id, {{ normalize_hebrew_club_name('home_team_name') }} as norm_name
    from {{ source('bronze', 'matches') }}
    where home_team_id is not null
    union
    select distinct away_team_id as team_id, {{ normalize_hebrew_club_name('away_team_name') }} as norm_name
    from {{ source('bronze', 'matches') }}
    where away_team_id is not null
),
appearances_names as (
    select distinct player_id, player_name
    from {{ source('bronze', 'appearances') }}
    where player_name is not null
    qualify row_number() over (partition by player_id order by match_id desc) = 1
),
enriched_players as (
    select
        cast(p.player_id as bigint) as player_id,
        coalesce(cast(p.full_name as varchar), an.player_name, 'שחקן ' || cast(p.player_id as varchar)) as full_name,
        cast(p.birth_date as date) as birth_date,
        coalesce(cast(p.nationality as varchar), 'ישראל') as nationality,
        coalesce(cast(p.current_team_id as bigint), mt.team_id) as current_team_id,
        cast(p.current_team_name as varchar) as current_team_name,
        {{ normalize_hebrew_club_name('p.current_team_name') }} as current_team_name_normalized,
        cast(p.current_league_id as bigint) as current_league_id,
        cast(p.current_league_name as varchar) as current_league_name,
        cast(p.scraped_at as timestamp) as scraped_at
    from raw_players p
    left join appearances_names an
        on p.player_id = an.player_id
    left join matches_teams mt
        on {{ normalize_hebrew_club_name('p.current_team_name') }} = mt.norm_name
    where p.player_id is not null
    qualify row_number() over (partition by p.player_id order by p.scraped_at desc) = 1
),
appearances_players as (
    select
        cast(a.player_id as bigint) as player_id,
        coalesce(cast(a.player_name as varchar), 'שחקן ' || cast(a.player_id as varchar)) as full_name,
        cast(null as date) as birth_date,
        'ישראל' as nationality,
        mt.team_id as current_team_id,
        cast(a.team_name as varchar) as current_team_name,
        {{ normalize_hebrew_club_name('a.team_name') }} as current_team_name_normalized,
        cast(a.league_id as bigint) as current_league_id,
        cast(null as varchar) as current_league_name,
        current_timestamp as scraped_at
    from {{ source('bronze', 'appearances') }} a
    left join matches_teams mt
        on {{ normalize_hebrew_club_name('a.team_name') }} = mt.norm_name
    where a.player_id is not null
      and a.player_id not in (select player_id from enriched_players)
    qualify row_number() over (partition by a.player_id order by a.match_id desc) = 1
)
select * from enriched_players
union all
select * from appearances_players
