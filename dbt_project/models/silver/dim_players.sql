{{ config(materialized='table') }}

with current_players as (
    select * from {{ ref('snap_players_scd2') }}
    where dbt_valid_to is null
)
select
    cast(player_id as bigint) as player_id,
    coalesce(cast(full_name as varchar), 'שחקן ' || cast(player_id as varchar)) as full_name,
    cast(birth_date as date) as birth_date,
    cast(date_part('year', birth_date) as integer) as birth_year,
    case 
        when birth_date is not null 
        then cast(date_diff('year', birth_date, current_date) as integer)
        else null 
    end as age,
    cast(nationality as varchar) as nationality,
    cast(current_team_id as bigint) as current_team_id,
    cast(current_league_id as bigint) as current_league_id,
    case 
        when birth_date is not null and date_diff('year', birth_date, current_date) <= 21 
        then true 
        else false 
    end as is_u21,
    case 
        when birth_date is not null and date_diff('year', birth_date, current_date) <= 23 
        then true 
        else false 
    end as is_u23,
    cast(dbt_valid_from as timestamp) as dbt_valid_from,
    cast(dbt_valid_to as timestamp) as dbt_valid_to,
    true as is_current
from current_players
