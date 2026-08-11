"""Tests for the data quality checks.

A check that never fails is worse than no check at all, so each test here
deliberately corrupts the warehouse and asserts the right check catches it.
"""

from __future__ import annotations

from dataclasses import replace

import duckdb
import pytest

from pipeline import quality, warehouse
from pipeline.config import FIXTURE_PATH, Config


@pytest.fixture()
def corruptible(cfg: Config, tmp_path) -> Config:
    """A private warehouse the test is free to break."""
    db_path = tmp_path / "corruptible.duckdb"
    scratch = replace(cfg, warehouse={**cfg.warehouse, "database": str(db_path)})
    warehouse.build(scratch, FIXTURE_PATH)
    return scratch


def _result(results: list[quality.CheckResult], name: str) -> quality.CheckResult:
    return next(r for r in results if r.name == name)


def test_clean_warehouse_passes_every_check(built_warehouse: Config) -> None:
    results = quality.run_checks(built_warehouse)
    failures = [r.name for r in results if not r.passed]
    assert failures == [], f"unexpected failures: {failures}"
    assert not quality.has_blocking_failure(results)


def test_results_are_persisted_for_auditing(built_warehouse: Config) -> None:
    quality.run_checks(built_warehouse)
    with duckdb.connect(str(built_warehouse.database_path), read_only=True) as con:
        rows = con.sql("SELECT COUNT(*) FROM dq_results").fetchone()[0]
    assert rows > 0


def test_out_of_range_price_is_caught(corruptible: Config) -> None:
    with duckdb.connect(str(corruptible.database_path)) as con:
        con.execute(
            """
            INSERT INTO fct_fuel_price_weekly
            SELECT * REPLACE (999.99 AS price_rm)
            FROM fct_fuel_price_weekly LIMIT 1
            """
        )
    results = quality.run_checks(corruptible)
    assert not _result(results, "price_within_plausible_range").passed
    assert quality.has_blocking_failure(results)


def test_duplicate_grain_is_caught(corruptible: Config) -> None:
    with duckdb.connect(str(corruptible.database_path)) as con:
        con.execute(
            "INSERT INTO fct_fuel_price_weekly SELECT * FROM fct_fuel_price_weekly LIMIT 1"
        )
    results = quality.run_checks(corruptible)
    assert not _result(results, "unique_grain").passed


def test_future_dated_row_is_caught(corruptible: Config) -> None:
    with duckdb.connect(str(corruptible.database_path)) as con:
        con.execute(
            """
            INSERT INTO fct_fuel_price_weekly
            SELECT * REPLACE (CURRENT_DATE + INTERVAL 30 DAY AS price_date)
            FROM fct_fuel_price_weekly LIMIT 1
            """
        )
    results = quality.run_checks(corruptible)
    assert not _result(results, "chronological_integrity").passed


def test_stale_source_is_caught(corruptible: Config) -> None:
    """Delete the recent weeks: the freshness check should notice."""
    with duckdb.connect(str(corruptible.database_path)) as con:
        con.execute(
            "DELETE FROM fct_fuel_price_weekly "
            "WHERE price_date > CURRENT_DATE - INTERVAL 120 DAY"
        )
    results = quality.run_checks(corruptible)
    assert not _result(results, "source_freshness").passed


def test_warnings_do_not_block_the_pipeline(corruptible: Config) -> None:
    """A suspicious-but-legal move is reported, not fatal."""
    with duckdb.connect(str(corruptible.database_path)) as con:
        con.execute(
            """
            UPDATE fct_fuel_price_weekly SET change_rm = 3.0
            WHERE price_date = (SELECT MAX(price_date) FROM fct_fuel_price_weekly)
              AND fuel_type = (SELECT MIN(fuel_type) FROM fct_fuel_price_weekly)
            """
        )
    results = quality.run_checks(corruptible)
    assert not _result(results, "implausible_weekly_move").passed
    assert not quality.has_blocking_failure(results)
