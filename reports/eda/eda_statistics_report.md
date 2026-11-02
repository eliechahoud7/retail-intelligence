# Advanced EDA and Statistical Inference (Phase 8)

**Notebooks:**
- `notebooks/04_advanced_eda.ipynb`: business EDA.
- `notebooks/05_statistical_inference.ipynb`: distributions, sampling, confidence intervals, hypothesis tests and an adjusted model.

**Data:** the validated Gold tables (`data/processed/analytical/`); nothing is recomputed from raw data.
**Environment:** project `.venv` (pandas 3.1, NumPy 2.5, SciPy 1.18, statsmodels 0.15, Matplotlib, Seaborn, Plotly); no new packages.

Notebooks 01–03 cover structure and data quality. These two notebooks add what was missing: business patterns and statistical evidence.

## Methodology
| Topic | Approach |
|---|---|
| Populations | Item-based order value: 98,666 orders with items (Q-03). Delivery: D-06 eligible orders, 96,470. Reviews: eligible orders with exactly one unshared review, 94,430, so one review per order and no invented order-level score (Q-04 / D-07). Time series: candidate window 2017-01 to 2018-08 (Q-11) |
| Descriptive statistics | Moment statistics (mean, variance, standard deviation, skewness, kurtosis) next to robust ones (median, trimmed and geometric mean, IQR, scaled MAD), because order value is heavy-tailed |
| Distributions | Normal vs log-normal by maximum likelihood (AIC, Q–Q, quantiles; KS distance only, since the KS p-value is uninformative at n ≈ 99,000). Daily counts: Poisson GLM vs negative binomial (dispersion, LR test) |
| Sampling | CLT simulation (5,000 samples per n); simple random vs proportionally stratified sampling (2,000 repetitions); fixed seed |
| Confidence intervals | t and bootstrap (mean), bootstrap (median), Wilson (rates), Newcomb (differences), log method (risk ratios), Fisher z (Spearman) |
| Hypothesis tests | 7 tests, each with question, H0/H1, assumption checks, statistic, p-value, effect size and confidence interval. Test choice follows the data: Mann–Whitney, two-proportion z, χ² independence, Spearman, Welch t on logs, Kruskal–Wallis. Holm correction across the family |
| Adjusted model | Logit for a 1–2 star review with HC1 robust standard errors, odds ratios, average marginal effect, VIF and AUC |
| A/B testing | No randomised comparison exists, so none is claimed. A power analysis sizes a future experiment instead |

**Validation:**
- All Gold row counts are asserted on load.
- The late rate equals the PostgreSQL view `analytics.delivery_monthly` (6,534 of 96,470).
- The z-statistic (122.3) and χ² (577.6) were recomputed by hand.
- The Spearman Fisher-z interval matches a bootstrap.
- The t and bootstrap CIs for the mean agree to within 0.02 BRL.
- Both notebooks execute top to bottom without errors or warnings.

## Key findings
| # | Finding | Evidence (95% CI) | Implication |
|---|---|---|---|
| 1 | **Lateness is the dominant driver of bad reviews** | 1–2 star share 62.3% late vs 9.2% on time; risk ratio 6.8 (6.6–7.0); adjusted OR 9.5 (8.9–10.3); average marginal effect +37 points | Protecting the delivery promise is the main satisfaction lever |
| 2 | **The cliff starts on the first day late; speed matters little** | 1–2 star share about 9% early → 32% at 1–3 days late → around 80% beyond a week late; on-time speed ρ = −0.13 | Realistic estimates for slow lanes beat faster delivery |
| 3 | Multi-seller and multi-item orders review worse | adjusted OR 4.1 and 3.1 | Investigate split shipments |
| 4 | Order value is log-normal-like, with a heavier tail | mean 138 vs median 87 BRL; log-normal p99 746 vs empirical 999 BRL | Use medians or log scale; don't trust a log-normal tail for planning |
| 5 | Demand is strongly weekly and overdispersed | weekday ε² = 0.53 (Monday +16%, Saturday −25%); variance 17× Poisson; Black Friday about 4.6× expected | Forecast with weekday effects, events and a negative-binomial error |
| 6 | Growth is volume-driven, not basket-driven | Jan–Aug 2018 vs 2017: orders +135%; monthly AOV 125–152 BRL | Acquisition growth; 2018 volume flat |
| 7 | Revenue is concentrated in few sellers and products | seller Gini 0.79; top 1% of sellers = 25.7% of revenue | Concentration risk |
| 8 | Repurchase is rare | 3.12% (3.01–3.23%) of people; 31% of second orders the same day | The business is acquisition-led |
| 9 | Nordeste is served worst | late rate 12.7% (12.0–13.4%) vs Sudeste 6.1%; per-order association weak (V = 0.08) | A regional logistics priority |
| 10 | Card buyers place larger orders | geometric mean +18.6% (16.9–20.4%) vs boleto | Self-selected: descriptive only |
| 11 | A late first delivery *may* reduce repurchase | −0.47 points (−0.84 to −0.01), p = 0.044 | Borderline: not established |

**Statistical vs practical significance:** all seven tests reject H0 after Holm correction, because the samples are very large. The findings are ranked by effect size: T3 (V = 0.08) and T4 (ρ = −0.13) are significant but weak.

## Limitations
- **Observational data:** every relationship is an association, not a causal effect. Product quality, seller reliability and customer income are unobserved confounders.
- **Review analyses:** cover eligible orders with exactly one unshared review (94,430 of 96,470). Reviews written before delivery (5%, mostly negative) are included. "Low review" (1–2 stars) is an analysis definition, not an approved KPI.
- **Timestamps:** used as recorded; the timezone is unknown (Q-14).
- **T7:** relies on a 180-day follow-up and only 54 events in the late group; it isn't adjusted for confounders.
- **Independence:** the tests assume independent orders (97% of people have one order). Same-seller clustering isn't modelled.
