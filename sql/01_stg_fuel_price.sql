-- Staging: reshape the published wide table (one column per fuel grade)
-- into a long, tidy table with one row per fuel grade per week.
--
-- {{ raw_table }}, {{ price_columns }} and {{ series_predicate }} are rendered
-- by pipeline/warehouse.py from the columns actually present in the snapshot,
-- so a new fuel grade appearing upstream flows through without a code change.

CREATE OR REPLACE TABLE stg_fuel_price AS
WITH source AS (
    SELECT *
    FROM {{ raw_table }}
    {{ series_predicate }}
),

unpivoted AS (
    SELECT *
    FROM (
        UNPIVOT source
        ON {{ price_columns }}
        INTO NAME fuel_type VALUE price_rm
    )
),

cleaned AS (
    SELECT
        CAST({{ date_column }} AS DATE)          AS price_date,
        lower(trim(fuel_type))                   AS fuel_type,
        CAST(price_rm AS DECIMAL(10, 4))         AS price_rm
    FROM unpivoted
    WHERE price_rm IS NOT NULL
      AND {{ date_column }} IS NOT NULL
)

-- Defensive de-duplication: if the publisher ever ships a week twice,
-- keep one row rather than double-counting it downstream.
SELECT DISTINCT
    price_date,
    fuel_type,
    price_rm
FROM cleaned
ORDER BY price_date, fuel_type;
