{#
    Schema of every dbt-built node (models, seeds, snapshots, stored test failures).

    * The layer schema is used EXACTLY as configured (dbt_staging, dbt_intermediate, dbt_marts)
      instead of dbt's default "<target schema>_<custom schema>".
    * PROTECTED: `warehouse` and `analytics` (and the schemas configured as the source schemas)
      belong to the Python pipeline - its loader, views and verify-warehouse / verify-views - so a
      node resolving to one of them is a compile error: dbt can never create, replace or drop
      anything there.
    * ALLOWED: only the three dbt schemas; any other name is also a compile error.
    Comparison is case- and whitespace-insensitive.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set protected = ['warehouse', 'analytics',
                         var('warehouse_schema', 'warehouse'), var('analytics_schema', 'analytics')]
                        | map('trim') | map('lower') | unique | list -%}
    {%- set allowed = ['dbt_staging', 'dbt_intermediate', 'dbt_marts'] -%}
    {%- set requested = custom_schema_name if custom_schema_name is not none else target.schema -%}
    {%- set schema = requested | string | trim | lower -%}
    {%- set where = node.unique_id if node is not none and node.unique_id is defined else 'this call' -%}
    {%- if schema in protected -%}
        {{ exceptions.raise_compiler_error(
            "generate_schema_name: schema '" ~ requested ~ "' (" ~ where ~ ") is PROTECTED - dbt must never "
            ~ "create, replace or drop objects in " ~ protected | join(', ')
            ~ ". Use one of " ~ allowed | join(', ') ~ ".") }}
    {%- elif schema not in allowed -%}
        {{ exceptions.raise_compiler_error(
            "generate_schema_name: schema '" ~ requested ~ "' (" ~ where ~ ") is not a dbt schema. "
            ~ "Allowed: " ~ allowed | join(', ') ~ ".") }}
    {%- endif -%}
    {{ schema }}
{%- endmacro %}
