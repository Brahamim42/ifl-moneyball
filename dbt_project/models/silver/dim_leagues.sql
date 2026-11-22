{{ config(materialized='table') }}

with target_leagues as (
    select * from (
        values
            (61, 28, 'Liga Alef North', 'ליגה א'' צפון', 'Senior Tier 3', 'Senior', 'North'),
            (62, 28, 'Liga Alef South', 'ליגה א'' דרום', 'Senior Tier 3', 'Senior', 'South'),
            (71, 28, 'Liga Bet North A', 'ליגה ב'' צפון א''', 'Senior Tier 4', 'Senior', 'North'),
            (72, 28, 'Liga Bet North B', 'ליגה ב'' צפון ב''', 'Senior Tier 4', 'Senior', 'North'),
            (73, 28, 'Liga Bet South A', 'ליגה ב'' דרום א''', 'Senior Tier 4', 'Senior', 'South'),
            (74, 28, 'Liga Bet South B', 'ליגה ב'' דרום ב''', 'Senior Tier 4', 'Senior', 'South'),
            (102, 28, 'Liga Leumit LaNoar North', 'ליגה לאומית לנוער צפון', 'U19 Tier 2', 'Youth', 'North'),
            (103, 28, 'Liga Leumit LaNoar South', 'ליגה לאומית לנוער דרום', 'U19 Tier 2', 'Youth', 'South'),
            (89, 28, 'Liga Gimel Central', 'ליגה ג'' מרכז', 'Senior Tier 5', 'Senior', 'Central'),
            (684, 28, 'Liga Gimel South', 'ליגה ג'' דרום', 'Senior Tier 5', 'Senior', 'South')
    ) as t(league_id, season_id, league_name_en, league_name_he, tier, category, region)
)
select
    cast(league_id as bigint) as league_id,
    cast(season_id as bigint) as season_id,
    cast(league_name_en as varchar) as league_name_en,
    cast(league_name_he as varchar) as league_name_he,
    cast(tier as varchar) as tier,
    cast(category as varchar) as category,
    cast(region as varchar) as region
from target_leagues
