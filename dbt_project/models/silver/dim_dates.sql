{{ config(materialized='table') }}

with date_range as (
    select unnest(generate_series(DATE '2024-01-01', DATE '2028-12-31', INTERVAL 1 DAY))::DATE as full_date
)
select
    cast(strftime(full_date, '%Y%m%d') as integer) as date_key,
    full_date,
    cast(dayofweek(full_date) as integer) as day_of_week,
    cast(strftime(full_date, '%A') as varchar) as day_name,
    case when dayofweek(full_date) in (5, 6) then true else false end as is_weekend_matchday,
    case when month(full_date) >= 7 then year(full_date) else year(full_date) - 1 end as season_year,
    case 
        when month(full_date) >= 7 
        then concat(cast(year(full_date) as varchar), '-', cast(year(full_date) + 1 as varchar))
        else concat(cast(year(full_date) - 1 as varchar), '-', cast(year(full_date) as varchar))
    end as season_label
from date_range
