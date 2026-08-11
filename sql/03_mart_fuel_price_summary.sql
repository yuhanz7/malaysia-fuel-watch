-- Mart: the "where are prices now?" table. One row per fuel grade,
-- putting the latest published price in the context of its own history.
--
-- Uses an ASOF JOIN to find the price as at the closest published week on or
-- before the same date one year earlier — the weekly series has gaps, so a
-- plain equi-join on date would silently drop grades.

CREATE OR REPLACE TABLE mart_fuel_price_summary AS
WITH latest AS (
    SELECT *
    FROM fct_fuel_price_weekly
    WHERE recency_rank = 1
),

history AS (
    SELECT
        fuel_type,
        COUNT(*)                                                     AS weeks_tracked,
        MIN(price_date)                                              AS first_price_date,
        MIN(price_rm)                                                AS all_time_low_rm,
        MAX(price_rm)                                                AS all_time_high_rm,
        SUM(CASE WHEN movement IN ('increase', 'decrease') THEN 1 ELSE 0 END) AS weeks_price_moved
    FROM fct_fuel_price_weekly
    GROUP BY fuel_type
),

window_52w AS (
    SELECT
        f.fuel_type,
        MIN(f.price_rm) AS low_52w_rm,
        MAX(f.price_rm) AS high_52w_rm,
        AVG(f.price_rm) AS avg_52w_rm
    FROM fct_fuel_price_weekly AS f
    INNER JOIN latest AS l
        ON f.fuel_type = l.fuel_type
       AND f.price_date > l.price_date - INTERVAL 52 WEEK
    GROUP BY f.fuel_type
),

year_ago AS (
    SELECT
        l.fuel_type,
        y.price_rm   AS price_1y_ago_rm,
        y.price_date AS price_1y_ago_date
    FROM latest AS l
    ASOF LEFT JOIN fct_fuel_price_weekly AS y
        ON l.fuel_type = y.fuel_type
       AND y.price_date <= l.price_date - INTERVAL 1 YEAR
)

SELECT
    l.fuel_type,
    l.price_date                                    AS latest_price_date,
    l.price_rm                                      AS latest_price_rm,
    l.change_rm                                     AS latest_change_rm,
    l.change_pct                                    AS latest_change_pct,
    l.movement                                      AS latest_movement,
    ROUND(w.low_52w_rm, 4)                          AS low_52w_rm,
    ROUND(w.high_52w_rm, 4)                         AS high_52w_rm,
    ROUND(w.avg_52w_rm, 4)                          AS avg_52w_rm,
    ROUND(
        100.0 * (l.price_rm - w.avg_52w_rm) / NULLIF(w.avg_52w_rm, 0), 2
    )                                               AS pct_vs_52w_avg,
    y.price_1y_ago_rm,
    ROUND(
        100.0 * (l.price_rm - y.price_1y_ago_rm) / NULLIF(y.price_1y_ago_rm, 0), 2
    )                                               AS yoy_change_pct,
    h.all_time_low_rm,
    h.all_time_high_rm,
    h.first_price_date,
    h.weeks_tracked,
    h.weeks_price_moved,
    ROUND(100.0 * h.weeks_price_moved / NULLIF(h.weeks_tracked - 1, 0), 1) AS pct_weeks_price_moved
FROM latest AS l
INNER JOIN history   AS h ON l.fuel_type = h.fuel_type
INNER JOIN window_52w AS w ON l.fuel_type = w.fuel_type
LEFT  JOIN year_ago  AS y ON l.fuel_type = y.fuel_type
ORDER BY l.fuel_type;
