-- name: delivery_region_ranking
-- grain: one Brazilian macro-region (Norte, Nordeste, Centro-Oeste, Sudeste, Sul), plus one
--        'Unknown' row for any customer state outside the 27 federative units
-- key: region
-- purpose: Delivery performance and rankings by macro-region: deliveries, average and median
--          duration, average delay, on-time and late rates.
-- competencies: inline reference table (VALUES), LEFT JOIN with an explicit unknown category,
--               conditional aggregation, percentile_cont, RANK, string_agg
-- notes: State-to-region mapping: the five official IBGE macro-regions (Grandes Regioes) for the
--        27 federative units; no project mapping existed. Unmapped state codes are NOT dropped: they
--        go to region 'Unknown' (always reported, 0 when there are none) and are not ranked.
--        Regional figures are ORDER-WEIGHTED: every eligible order counts once, so large states
--        (e.g. SP in Sudeste) dominate their region; they are not averages of state averages.
--        Eligibility and definitions as in delivery_monthly_performance (D-06; duration =
--        purchase_to_delivery_days; delay = delivered date - estimated date; on-time = delay <= 0).
--        RANKING METRICS: speed_rank 1 = shortest AVERAGE duration (avg_delivery_days);
--        punctuality_rank 1 = highest on-time rate. delivery_state_performance ranks states by
--        MEDIAN duration instead, so the region and state speed ranks use different metrics and
--        are not directly comparable. No analysis window.
WITH region_map (state, region) AS (
    VALUES ('AC', 'Norte'), ('AP', 'Norte'), ('AM', 'Norte'), ('PA', 'Norte'), ('RO', 'Norte'), ('RR', 'Norte'),
           ('TO', 'Norte'),
           ('AL', 'Nordeste'), ('BA', 'Nordeste'), ('CE', 'Nordeste'), ('MA', 'Nordeste'), ('PB', 'Nordeste'),
           ('PE', 'Nordeste'), ('PI', 'Nordeste'), ('RN', 'Nordeste'), ('SE', 'Nordeste'),
           ('DF', 'Centro-Oeste'), ('GO', 'Centro-Oeste'), ('MT', 'Centro-Oeste'), ('MS', 'Centro-Oeste'),
           ('ES', 'Sudeste'), ('MG', 'Sudeste'), ('RJ', 'Sudeste'), ('SP', 'Sudeste'),
           ('PR', 'Sul'), ('RS', 'Sul'), ('SC', 'Sul')
), regions (region) AS (
    SELECT DISTINCT region FROM region_map UNION ALL SELECT 'Unknown'
), eligible AS (
    SELECT coalesce(m.region, 'Unknown')                                  AS region,
           o.customer_state_std,
           o.purchase_to_delivery_days,
           o.delivery_vs_estimate_days
    FROM {w}.fact_orders o
    LEFT JOIN region_map m ON m.state = o.customer_state_std
    WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
), by_region AS (
    SELECT region,
           count(*)                                                       AS n_delivered,
           count(*) FILTER (WHERE delivery_vs_estimate_days > 0)          AS n_late,
           count(DISTINCT customer_state_std)                             AS n_states,
           string_agg(DISTINCT customer_state_std, ', ' ORDER BY customer_state_std) AS states,
           round(avg(purchase_to_delivery_days)::numeric, 2)              AS avg_delivery_days,
           round((percentile_cont(0.5) WITHIN GROUP (ORDER BY purchase_to_delivery_days))::numeric, 2) AS median_delivery_days,
           round(avg(delivery_vs_estimate_days)::numeric, 2)              AS avg_delay_days
    FROM eligible
    GROUP BY region
), metrics AS (
    SELECT r.region,
           coalesce(b.n_delivered, 0)                                     AS n_delivered,
           coalesce(b.n_late, 0)                                          AS n_late,
           coalesce(b.n_states, 0)                                        AS n_states,
           b.states,
           round(1.0 * (b.n_delivered - b.n_late) / nullif(b.n_delivered, 0), 4) AS on_time_rate,
           round(1.0 * b.n_late / nullif(b.n_delivered, 0), 4)            AS late_rate,
           b.avg_delivery_days, b.median_delivery_days, b.avg_delay_days
    FROM regions r
    LEFT JOIN by_region b ON b.region = r.region
)
SELECT region,
       n_delivered,
       round(100.0 * n_delivered / nullif(sum(n_delivered) OVER (), 0), 2)  AS share_of_deliveries_pct,
       n_states,
       states,
       n_late,
       on_time_rate,
       late_rate,
       avg_delivery_days,
       median_delivery_days,
       avg_delay_days,
       CASE WHEN region <> 'Unknown' AND n_delivered > 0
            THEN rank() OVER (PARTITION BY region = 'Unknown' OR n_delivered = 0 ORDER BY avg_delivery_days ASC) END AS speed_rank,
       CASE WHEN region <> 'Unknown' AND n_delivered > 0
            THEN rank() OVER (PARTITION BY region = 'Unknown' OR n_delivered = 0 ORDER BY on_time_rate DESC) END    AS punctuality_rank
FROM metrics
ORDER BY region = 'Unknown', speed_rank NULLS LAST, region;
