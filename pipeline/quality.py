"""Data quality step: assert the warehouse is trustworthy before publishing.

Each check is a SQL statement that returns the number of offending rows.
Zero means the check passed. Results are written back into the warehouse as
``dq_results`` so the history of every run is queryable, and the summary is
embedded in the generated report.

Severity levels
---------------
error : breaks the contract downstream consumers rely on. Fails the run.
warn  : suspicious but legitimate (e.g. an unusually large weekly move).
        Surfaced in the report, never blocks the pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import duckdb

from pipeline.config import Config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Check:
    name: str
    severity: str
    description: str
    sql: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    severity: str
    description: str
    failing_rows: int

    @property
    def passed(self) -> bool:
        return self.failing_rows == 0


def build_checks(cfg: Config) -> list[Check]:
    q = cfg.quality
    return [
        Check(
            name="not_null_core_fields",
            severity="error",
            description="Every staged row has a date, a fuel grade and a price.",
            sql="""
                SELECT COUNT(*) FROM stg_fuel_price
                WHERE price_date IS NULL OR fuel_type IS NULL OR price_rm IS NULL
            """,
        ),
        Check(
            name="unique_grain",
            severity="error",
            description="The fact table has exactly one row per fuel grade per week.",
            sql="""
                SELECT COUNT(*) FROM (
                    SELECT price_date, fuel_type
                    FROM fct_fuel_price_weekly
                    GROUP BY price_date, fuel_type
                    HAVING COUNT(*) > 1
                )
            """,
        ),
        Check(
            name="non_empty_fact",
            severity="error",
            description="The fact table is not empty.",
            sql="SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END FROM fct_fuel_price_weekly",
        ),
        Check(
            name="price_within_plausible_range",
            severity="error",
            description=(
                f"Prices sit between RM{q['price_min_rm']:.2f} and "
                f"RM{q['price_max_rm']:.2f} per litre."
            ),
            sql=f"""
                SELECT COUNT(*) FROM fct_fuel_price_weekly
                WHERE price_rm < {q['price_min_rm']} OR price_rm > {q['price_max_rm']}
            """,
        ),
        Check(
            name="chronological_integrity",
            severity="error",
            description="No observation is dated in the future.",
            sql="SELECT COUNT(*) FROM fct_fuel_price_weekly WHERE price_date > CURRENT_DATE",
        ),
        Check(
            name="source_freshness",
            severity="error",
            description=(
                f"The latest observation is under {q['max_days_since_latest']} days old."
            ),
            sql=f"""
                SELECT CASE
                    WHEN date_diff('day', MAX(price_date), CURRENT_DATE)
                         > {q['max_days_since_latest']}
                    THEN 1 ELSE 0 END
                FROM fct_fuel_price_weekly
            """,
        ),
        Check(
            name="implausible_weekly_move",
            severity="warn",
            description=(
                f"No week-on-week move exceeds RM{q['max_weekly_move_rm']:.2f} per litre."
            ),
            sql=f"""
                SELECT COUNT(*) FROM fct_fuel_price_weekly
                WHERE abs(change_rm) > {q['max_weekly_move_rm']}
            """,
        ),
        Check(
            name="reporting_gaps",
            severity="warn",
            description="No gap longer than 21 days between consecutive observations.",
            sql="SELECT COUNT(*) FROM fct_fuel_price_weekly WHERE days_since_prev > 21",
        ),
    ]


def run_checks(cfg: Config) -> list[CheckResult]:
    """Execute every check and persist the results into the warehouse."""
    checks = build_checks(cfg)
    results: list[CheckResult] = []
    run_at = datetime.now(timezone.utc)

    with duckdb.connect(str(cfg.database_path)) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS dq_results (
                run_at        TIMESTAMPTZ,
                check_name    VARCHAR,
                severity      VARCHAR,
                description   VARCHAR,
                failing_rows  BIGINT,
                passed        BOOLEAN
            )
            """
        )
        for check in checks:
            failing = con.sql(check.sql).fetchone()[0] or 0
            result = CheckResult(check.name, check.severity, check.description, int(failing))
            results.append(result)
            con.execute(
                "INSERT INTO dq_results VALUES (?, ?, ?, ?, ?, ?)",
                [
                    run_at,
                    result.name,
                    result.severity,
                    result.description,
                    result.failing_rows,
                    result.passed,
                ],
            )
            status = "PASS" if result.passed else result.severity.upper()
            log.log(
                logging.INFO if result.passed else logging.WARNING,
                "  [%-5s] %-30s %s",
                status,
                result.name,
                "" if result.passed else f"{result.failing_rows} offending row(s)",
            )

    return results


def has_blocking_failure(results: list[CheckResult]) -> bool:
    return any(r.severity == "error" and not r.passed for r in results)
