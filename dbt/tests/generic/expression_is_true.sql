{#
    Generic test: `expression` (a SQL boolean over the model's columns) is true for every row.
    A row where it is false or NULL fails. Read-only: a single SELECT, nothing is created.
#}
{% test expression_is_true(model, expression) %}
    select *
    from {{ model }}
    where not coalesce(({{ expression }}), false)
{% endtest %}
