"""Tests for column detection, SQL rendering and the transform models."""

from __future__ import annotations

import pandas as pd
import pytest

from pipeline import warehouse
from pipeline.config import Config
from pipeline.warehouse import build_template_context, detect_price_columns, render


# --- column detection ---------------------------------------------------------


def test_detect_price_columns_ignores_metadata(cfg: Config) -> None:
    frame = pd.DataFrame(
        {"date": ["2024-01-04"], "series_type": ["level"], "ron95": [2.05], "diesel": [3.35]}
    )
    assert detect_price_columns(frame, cfg) == ["ron95", "diesel"]


def test_detect_price_columns_picks_up_new_grades(cfg: Config) -> None:
    """A grade added upstream should flow through without a code change."""
    frame = pd.DataFrame(
        {
            "date": ["2024-01-04"],
            "series_type": ["level"],
            "ron95": [2.05],
            "ron100": [4.20],  # hypothetical new grade
        }
    )
    assert "ron100" in detect_price_columns(frame, cfg)


def test_detect_price_columns_raises_when_source_has_none(cfg: Config) -> None:
    frame = pd.DataFrame({"date": ["2024-01-04"], "series_type": ["level"]})
    with pytest.raises(ValueError, match="No price columns"):
        detect_price_columns(frame, cfg)


# --- SQL templating -----------------------------------------------------------


def test_render_substitutes_placeholders() -> None:
    assert render("SELECT * FROM {{ raw_table }}", {"raw_table": "raw_x"}) == "SELECT * FROM raw_x"


def test_render_rejects_unknown_placeholder() -> None:
    with pytest.raises(KeyError):
        render("SELECT {{ mystery }}", {"raw_table": "raw_x"})


def test_series_filter_applied_when_column_present(cfg: Config) -> None:
    frame = pd.DataFrame({"date": ["2024-01-04"], "series_type": ["level"], "ron95": [2.05]})
    assert "series_type = 'level'" in build_template_context(frame, cfg)["series_predicate"]


def test_series_filter_skipped_when_column_absent(cfg: Config) -> None:
    """The source dropping its series column must not break the build."""
    frame = pd.DataFrame({"date": ["2024-01-04"], "ron95": [2.05]})
    assert build_template_context(frame, cfg)["series_predicate"] == ""


# --- model outputs ------------------------------------------------------------


def test_all_models_are_created(built_warehouse: Config) -> None:
    tables = warehouse.query(built_warehouse, "SHOW TABLES")["name"].tolist()
    for expected in (
        "stg_fuel_price",
        "fct_fuel_price_weekly",
        "mart_fuel_price_summary",
        "mart_annual_stats",
        "mart_biggest_moves",
    ):
        assert expected in tables


def test_change_series_is_excluded_from_staging(built_warehouse: Config) -> None:
    """Only 'level' rows should survive staging — 'change' rows are deltas."""
    raw_weeks = warehouse.query(
        built_warehouse,
        "SELECT COUNT(DISTINCT date) AS n FROM raw_fuel_price WHERE series_type = 'level'",
    )["n"][0]
    staged_weeks = warehouse.query(
        built_warehouse, "SELECT COUNT(DISTINCT price_date) AS n FROM stg_fuel_price"
    )["n"][0]
    assert staged_weeks == raw_weeks


def test_fact_grain_is_one_row_per_grade_per_week(built_warehouse: Config) -> None:
    duplicates = warehouse.query(
        built_warehouse,
        """
        SELECT COUNT(*) AS n FROM (
            SELECT price_date, fuel_type FROM fct_fuel_price_weekly
            GROUP BY price_date, fuel_type HAVING COUNT(*) > 1
        )
        """,
    )["n"][0]
    assert duplicates == 0


def test_week_on_week_change_matches_price_difference(built_warehouse: Config) -> None:
    mismatches = warehouse.query(
        built_warehouse,
        """
        SELECT COUNT(*) AS n FROM fct_fuel_price_weekly
        WHERE prev_price_rm IS NOT NULL
          AND abs((price_rm - prev_price_rm) - change_rm) > 0.0001
        """,
    )["n"][0]
    assert mismatches == 0


def test_movement_label_agrees_with_the_numbers(built_warehouse: Config) -> None:
    inconsistent = warehouse.query(
        built_warehouse,
        """
        SELECT COUNT(*) AS n FROM fct_fuel_price_weekly
        WHERE (movement = 'increase'  AND change_rm <= 0)
           OR (movement = 'decrease'  AND change_rm >= 0)
           OR (movement = 'unchanged' AND change_rm <> 0)
        """,
    )["n"][0]
    assert inconsistent == 0


def test_summary_has_one_row_per_grade(built_warehouse: Config) -> None:
    grades = warehouse.query(
        built_warehouse, "SELECT COUNT(DISTINCT fuel_type) AS n FROM fct_fuel_price_weekly"
    )["n"][0]
    summary_rows = warehouse.query(
        built_warehouse, "SELECT COUNT(*) AS n FROM mart_fuel_price_summary"
    )["n"][0]
    assert summary_rows == grades


def test_summary_latest_price_matches_the_fact_table(built_warehouse: Config) -> None:
    mismatches = warehouse.query(
        built_warehouse,
        """
        SELECT COUNT(*) AS n
        FROM mart_fuel_price_summary AS s
        INNER JOIN (
            SELECT fuel_type, price_rm, price_date
            FROM fct_fuel_price_weekly
            QUALIFY ROW_NUMBER() OVER (PARTITION BY fuel_type ORDER BY price_date DESC) = 1
        ) AS f
            ON s.fuel_type = f.fuel_type
        WHERE s.latest_price_rm <> f.price_rm OR s.latest_price_date <> f.price_date
        """,
    )["n"][0]
    assert mismatches == 0


def test_annual_stats_cover_every_year_in_the_fact_table(built_warehouse: Config) -> None:
    fact_years = warehouse.query(
        built_warehouse, "SELECT COUNT(DISTINCT price_year) AS n FROM fct_fuel_price_weekly"
    )["n"][0]
    mart_years = warehouse.query(
        built_warehouse, "SELECT COUNT(DISTINCT price_year) AS n FROM mart_annual_stats"
    )["n"][0]
    assert mart_years == fact_years
