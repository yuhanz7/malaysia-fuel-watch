-- Fact table: one row per fuel grade per week, enriched with the
-- week-on-week movement and rolling context that every downstream
-- mart and chart needs.

CREATE OR REPLACE TABLE fct_fuel_price_weekly AS
WITH ordered AS (
    SELECT
        price_date,
        fuel_type,
        price_rm,
        LAG(price_rm) OVER (PARTITION BY fuel_type ORDER BY price_date) AS prev_price_rm,
        LAG(price_date) OVER (PARTITION BY fuel_type ORDER BY price_date) AS prev_price_date,
        AVG(price_rm) OVER (
            PARTITION BY fuel_type ORDER BY price_date
            ROWS BETWEEN 51 PRECEDING AND CURRENT ROW
        ) AS rolling_avg_52w,
        ROW_NUMBER() OVER (PARTITION BY fuel_type ORDER BY price_date DESC) AS recency_rank
    FROM stg_fuel_price
)

SELECT
    price_date,
    fuel_type,
    EXTRACT(YEAR FROM price_date)                       AS price_year,
    EXTRACT(MONTH FROM price_date)                      AS price_month,
    price_rm,
    prev_price_rm,
    date_diff('day', prev_price_date, price_date)       AS days_since_prev,
    ROUND(price_rm - prev_price_rm, 4)                  AS change_rm,
    ROUND(
        100.0 * (price_rm - prev_price_rm) / NULLIF(prev_price_rm, 0), 3
    )                                                   AS change_pct,
    CASE
        WHEN prev_price_rm IS NULL          THEN 'first_observation'
        WHEN price_rm > prev_price_rm       THEN 'increase'
        WHEN price_rm < prev_price_rm       THEN 'decrease'
        ELSE 'unchanged'
    END                                                 AS movement,
    ROUND(rolling_avg_52w, 4)                           AS rolling_avg_52w,
    recency_rank
FROM ordered
ORDER BY price_date, fuel_type;
