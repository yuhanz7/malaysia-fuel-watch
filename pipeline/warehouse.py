"""Transform step: load the raw snapshot into DuckDB and run the SQL models.

This is a deliberately small, dbt-flavoured runner: SQL lives in ``sql/`` as
numbered models, Python only renders a handful of ``{{ placeholders }}`` and
executes the files in order. Business logic stays in SQL where it can be read,
reviewed and reused; orchestration stays in Python.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import pandas as pd

from pipeline.config import Config, SQL_DIR

log = logging.getLogger(__name__)

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def detect_price_columns(frame: pd.DataFrame, cfg: Config) -> list[str]:
    """Return the numeric columns that represent a fuel grade.

    Anything numeric that is not explicitly excluded in config counts, so a new
    grade published upstream is picked up automatically instead of being
    silently dropped by a hardcoded column list.
    """
    excluded = {c.lower() for c in cfg.non_price_columns}
    price_columns = [
        column
        for column in frame.columns
        if column.lower() not in excluded
        and pd.api.types.is_numeric_dtype(frame[column])
    ]
    if not price_columns:
        raise ValueError(
            "No price columns detected in the source. Columns were: "
            f"{list(frame.columns)}"
        )
    log.info("Detected %d price series: %s", len(price_columns), ", ".join(price_columns))
    return price_columns


def build_template_context(frame: pd.DataFrame, cfg: Config) -> dict[str, str]:
    """Values substituted into the SQL models."""
    price_columns = detect_price_columns(frame, cfg)

    series_predicate = ""
    series_column = cfg.series_column
    if series_column and series_column in frame.columns and cfg.series_filter:
        series_predicate = f"WHERE {series_column} = '{cfg.series_filter}'"
        log.info("Filtering raw rows with: %s", series_predicate)

    return {
        "raw_table": cfg.raw_table,
        "date_column": cfg.date_column,
        "price_columns": ", ".join(price_columns),
        "series_predicate": series_predicate,
    }


def render(sql_text: str, context: dict[str, str]) -> str:
    """Substitute ``{{ name }}`` placeholders, failing loudly on unknown names."""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise KeyError(f"SQL model references unknown placeholder '{{{{ {key} }}}}'")
        return context[key]

    return PLACEHOLDER.sub(_replace, sql_text)


def model_files(sql_dir: Path = SQL_DIR) -> list[Path]:
    """SQL models in execution order (files are numbered)."""
    return sorted(sql_dir.glob("*.sql"))


def build(cfg: Config, snapshot: Path) -> dict[str, int]:
    """Build the warehouse from a raw snapshot. Returns row counts per model."""
    cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.read_parquet(snapshot)
    context = build_template_context(frame, cfg)

    row_counts: dict[str, int] = {}
    with duckdb.connect(str(cfg.database_path)) as con:
        con.register("raw_input", frame)
        con.execute(f"CREATE OR REPLACE TABLE {cfg.raw_table} AS SELECT * FROM raw_input")
        row_counts[cfg.raw_table] = con.sql(
            f"SELECT COUNT(*) FROM {cfg.raw_table}"
        ).fetchone()[0]

        for path in model_files():
            log.info("Running model %s", path.name)
            con.execute(render(path.read_text(encoding="utf-8"), context))
            table = path.stem.split("_", 1)[1]
            row_counts[table] = con.sql(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    for table, count in row_counts.items():
        log.info("  %-28s %8d rows", table, count)
    return row_counts


def query(cfg: Config, sql: str) -> pd.DataFrame:
    """Convenience read-only query against the built warehouse."""
    with duckdb.connect(str(cfg.database_path), read_only=True) as con:
        return con.sql(sql).df()
