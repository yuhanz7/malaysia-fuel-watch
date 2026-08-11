-- Mart: the largest single-week price moves on record, ranked per fuel grade.
-- Doubles as an anomaly feed — a genuinely new record move is worth a look
-- before it is reported as fact.

CREATE OR REPLACE TABLE mart_biggest_moves AS
WITH ranked AS (
    SELECT
        price_date,
        fuel_type,
        prev_price_rm,
        price_rm,
        change_rm,
        change_pct,
        movement,
        ROW_NUMBER() OVER (
            PARTITION BY fuel_type
            ORDER BY abs(change_rm) DESC, price_date DESC
        ) AS move_rank
    FROM fct_fuel_price_weekly
    WHERE change_rm IS NOT NULL
      AND change_rm <> 0
)

SELECT *
FROM ranked
WHERE move_rank <= 5
ORDER BY fuel_type, move_rank;
