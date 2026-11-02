-- Monthly percentage shares of mart_payment_methods_monthly (fails with one row per bad month):
-- * the payment-type shares of a month add up to 100 %, allowing only the rounding of the shares
--   (each is rounded to 4 dp, so at most 0.00005 per type; tolerance 0.001 % for <= 5 types);
-- * month_payment_value_total is identical on every row of the month and equals the sum of the
--   month's payment_value_total (exactly - both are sums of numeric(12,2) values);
-- * the Q-11 flag is constant within a month (it is defined by the purchase month).
select
    purchase_year_month,
    sum(payment_value_share_pct)                    as share_pct_sum,
    sum(payment_value_total)                        as type_value_sum,
    min(month_payment_value_total)                  as month_total_min,
    max(month_payment_value_total)                  as month_total_max,
    count(distinct in_candidate_window)             as n_window_flags
from {{ ref('mart_payment_methods_monthly') }}
group by purchase_year_month
having abs(sum(payment_value_share_pct) - 100) > 0.001
    or min(month_payment_value_total) <> max(month_payment_value_total)
    or min(month_payment_value_total) <> sum(payment_value_total)
    or count(distinct in_candidate_window) <> 1
