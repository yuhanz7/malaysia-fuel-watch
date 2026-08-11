"""Configuration loading and path resolution.

All paths in the project are derived from PROJECT_ROOT so the pipeline
behaves the same whether it is run from the repo root, from an IDE, or
from a GitHub Actions runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_PATH = PROJECT_ROOT / "config" / "sources.yml"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SQL_DIR = PROJECT_ROOT / "sql"
DOCS_DIR = PROJECT_ROOT / "docs"
CHART_DIR = DOCS_DIR / "charts"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "fuel_price_sample.parquet"


@dataclass(frozen=True)
class Config:
    """Typed view over config/sources.yml."""

    source: dict[str, Any]
    schema: dict[str, Any]
    warehouse: dict[str, Any]
    quality: dict[str, Any]

    # --- convenience accessors -------------------------------------------------
    @property
    def source_name(self) -> str:
        return self.source["name"]

    @property
    def source_url(self) -> str:
        return self.source["url"]

    @property
    def date_column(self) -> str:
        return self.schema["date_column"]

    @property
    def series_column(self) -> str | None:
        return self.schema.get("series_column")

    @property
    def series_filter(self) -> str | None:
        return self.schema.get("series_filter")

    @property
    def non_price_columns(self) -> list[str]:
        return list(self.schema.get("non_price_columns", []))

    @property
    def raw_table(self) -> str:
        return self.warehouse["raw_table"]

    @property
    def database_path(self) -> Path:
        return PROJECT_ROOT / self.warehouse["database"]

    @property
    def raw_source_dir(self) -> Path:
        return RAW_DIR / self.source_name


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Read the YAML config into a Config object."""
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return Config(
        source=raw["source"],
        schema=raw["schema"],
        warehouse=raw["warehouse"],
        quality=raw["quality"],
    )
