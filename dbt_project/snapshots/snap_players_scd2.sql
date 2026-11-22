{% snapshot snap_players_scd2 %}

{{
    config(
        target_schema='main',
        unique_key='player_id',
        strategy='check',
        check_cols=['current_team_id', 'current_league_id']
    )
}}

select
    player_id,
    full_name,
    birth_date,
    nationality,
    current_team_id,
    current_team_name,
    current_team_name_normalized,
    current_league_id,
    current_league_name,
    scraped_at
from {{ ref('stg_players') }}

{% endsnapshot %}
