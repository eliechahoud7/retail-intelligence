{#
    Generic test - row preservation: `model` and `source_relation` contain the same multiset of rows
    over all source columns (EXCEPT ALL in both directions). Proves that no row was filtered, dropped
    or duplicated and that no value was changed - which also implies equal row counts.
    Fails with one row per surplus / missing row (side = 'only_in_model' / 'only_in_source').
    Read-only: a single SELECT, nothing is created.
#}
{% test matches_source_rows(model, source_relation) %}
    {%- set columns = adapter.get_columns_in_relation(source_relation) -%}
    {%- if execute and columns | length == 0 -%}  {#- at parse time the adapter returns no columns -#}
        {{ exceptions.raise_compiler_error("matches_source_rows: no columns found for " ~ source_relation) }}
    {%- endif -%}
    {%- set column_list = columns | map(attribute='name') | map('replace', '"', '""')
                          | map('string') | list -%}
    {%- set select_list -%}
        {%- for c in column_list %}"{{ c }}"{{ ", " if not loop.last }}{% endfor -%}
    {%- endset %}
    (select 'only_in_model' as side, {{ select_list }} from {{ model }}
     except all
     select 'only_in_model', {{ select_list }} from {{ source_relation }})
    union all
    (select 'only_in_source' as side, {{ select_list }} from {{ source_relation }}
     except all
     select 'only_in_source', {{ select_list }} from {{ model }})
{% endtest %}
