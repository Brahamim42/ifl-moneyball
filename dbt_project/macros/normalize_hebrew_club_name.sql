{% macro normalize_hebrew_club_name(column_name) %}
case 
    when {{ column_name }} is null then null
    else trim(regexp_replace(
        regexp_replace(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(
                            regexp_replace(
                                -- 1. Trim surrounding quotes and whitespace, collapse multiple spaces
                                regexp_replace(trim({{ column_name }}, ' "' || chr(39) || '`'), '\s+', ' ', 'g'),
                                -- 2. Standardize הפועל (e.g. הפועל, הפ', הפ)
                                '^(הפועל|הפ' || chr(39) || '|הפ)\s+', 'הפועל '
                            ),
                            -- 3. Standardize מכבי (e.g. מכבי, מכ', מכ)
                            '^(מכבי|מכ' || chr(39) || '|מכ)\s+', 'מכבי '
                        ),
                        -- 4. Standardize מ.ס. (e.g. מ.ס., מ.ס, מועדון ספורט)
                        '^(מ\.ס\.?|מ ס|מועדון ספורט)\s+', 'מ.ס. '
                    ),
                    -- 5. Standardize מ.כ. (e.g. מ.כ., מ.כ, מועדון כדורגל)
                    '^(מ\.כ\.?|מ כ|מועדון כדורגל)\s+', 'מ.כ. '
                ),
                -- 6. Standardize בית"ר (e.g. בית"ר, בית'ר, ביתר)
                '^(בית["' || chr(39) || ']?ר|ביתר)\s+', 'בית"ר '
            ),
            -- 7. Standardize עירוני (e.g. עירוני, ע.)
            '^(עירוני|ע\.)\s+', 'עירוני '
        ),
        -- 8. Strip sponsor / commercial suffixes at the end
        '\s*[-–/]?\s*(בע"מ|בע' || chr(39) || 'מ|נדל"ן|נדל' || chr(39) || 'ן|ביטוח|קבוצת|פיננסים|רכב|אחמד)$', ''
    ))
end
{% endmacro %}

{% macro extract_hebrew_club_prefix(column_name) %}
case
    when {{ column_name }} is null then null
    when {{ normalize_hebrew_club_name(column_name) }} like 'מ.ס. %' then 'מ.ס.'
    when {{ normalize_hebrew_club_name(column_name) }} like 'מ.כ. %' then 'מ.כ.'
    when {{ normalize_hebrew_club_name(column_name) }} like 'הפועל %' then 'הפועל'
    when {{ normalize_hebrew_club_name(column_name) }} like 'מכבי %' then 'מכבי'
    when {{ normalize_hebrew_club_name(column_name) }} like 'עירוני %' then 'עירוני'
    when {{ normalize_hebrew_club_name(column_name) }} like 'בית"ר %' then 'בית"ר'
    else null
end
{% endmacro %}
