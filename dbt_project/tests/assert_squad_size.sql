-- Singular test: Verify match squad size is realistic (asserting 18 <= squad_rows_per_match <= 32)
with match_squads as (
    select
        match_id,
        count(*) as squad_rows_per_match
    from {{ ref('fact_match_appearances') }}
    where minutes_played > 0
    group by match_id
)
select
    match_id,
    squad_rows_per_match
from match_squads
where squad_rows_per_match < 18 or squad_rows_per_match > 32
