{#
    Generic test: the combination of `combination_of_columns` is unique in the model / source
    (composite primary keys and grain guards). Fails with one row per duplicated combination.
    Read-only: a single SELECT, nothing is created.
#}
{% test unique_combination_of_columns(model, combination_of_columns) %}
    {%- set columns = combination_of_columns | join(', ') -%}
    select {{ columns }}, count(*) as n_rows
    from {{ model }}
    group by {{ columns }}
    having count(*) > 1
{% endtest %}
