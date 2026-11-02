{#
    Generic test - column contract: `model` has exactly the columns of `source_relation`, with the
    same names, in the same order, with the same PostgreSQL type including the type modifier
    (format_type, e.g. numeric(12,2), timestamp(0) without time zone, integer[]).
    Fails with one row per missing, extra, renamed, reordered or retyped column.
    Read-only: a catalog query, nothing is created.
#}
{% test matches_source_columns(model, source_relation) %}
    {%- set m = adapter.quote(model.schema) ~ '.' ~ adapter.quote(model.identifier) -%}
    {%- set s = adapter.quote(source_relation.schema) ~ '.' ~ adapter.quote(source_relation.identifier) -%}
    with source_columns as (
        select attnum as position, attname as column_name, format_type(atttypid, atttypmod) as data_type
        from pg_catalog.pg_attribute
        where attrelid = to_regclass('{{ s }}') and attnum > 0 and not attisdropped
    ), model_columns as (
        select attnum as position, attname as column_name, format_type(atttypid, atttypmod) as data_type
        from pg_catalog.pg_attribute
        where attrelid = to_regclass('{{ m }}') and attnum > 0 and not attisdropped
    )
    select coalesce(s.position, m.position) as position,
           s.column_name as source_column, s.data_type as source_type,
           m.column_name as model_column,  m.data_type as model_type
    from source_columns s
    full outer join model_columns m on m.position = s.position
    where s.column_name is distinct from m.column_name
       or s.data_type   is distinct from m.data_type
    union all
    -- a relation that cannot be resolved has no columns: report it instead of passing vacuously
    select null, '{{ s }}', 'relation not found', null, null where to_regclass('{{ s }}') is null
    union all
    select null, null, null, '{{ m }}', 'relation not found' where to_regclass('{{ m }}') is null
{% endtest %}
