-- Mart: year-by-year behaviour of each fuel grade.
-- `volatility_rm` is the standard deviation of the weekly price within the
-- year: a cheap but effective proxy for how much the pump price moved around.

CREATE OR REPLACE TABLE mart_annual_stats AS
WITH bounds AS (
    SELECT
        price_year,
        fuel_type,
        FIRST(price_rm ORDER BY price_date)  AS first_price_rm,
        LAST(price_rm ORDER BY price_date)   AS last_price_rm
    FROM fct_fuel_price_weekly
    GROUP BY price_year, fuel_type
)

SELECT
    f.price_year,
    f.fuel_type,
    COUNT(*)                                            AS weeks_observed,
    ROUND(AVG(f.price_rm), 4)                           AS avg_price_rm,
    MIN(f.price_rm)                                     AS min_price_rm,
    MAX(f.price_rm)                                     AS max_price_rm,
    ROUND(MAX(f.price_rm) - MIN(f.price_rm), 4)         AS price_range_rm,
    ROUND(STDDEV_SAMP(f.price_rm), 4)                   AS volatility_rm,
    b.first_price_rm,
    b.last_price_rm,
    ROUND(b.last_price_rm - b.first_price_rm, 4)        AS net_change_rm,
    ROUND(
        100.0 * (b.last_price_rm - b.first_price_rm) / NULLIF(b.first_price_rm, 0), 2
    )                                                   AS net_change_pct,
    SUM(CASE WHEN f.movement = 'increase' THEN 1 ELSE 0 END) AS weeks_up,
    SUM(CASE WHEN f.movement = 'decrease' THEN 1 ELSE 0 END) AS weeks_down,
    SUM(CASE WHEN f.movement = 'unchanged' THEN 1 ELSE 0 END) AS weeks_flat
FROM fct_fuel_price_weekly AS f
INNER JOIN bounds AS b
    ON f.price_year = b.price_year
   AND f.fuel_type = b.fuel_type
GROUP BY f.price_year, f.fuel_type, b.first_price_rm, b.last_price_rm
ORDER BY f.price_year, f.fuel_type;
