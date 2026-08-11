"""Shared test fixtures.

Tests run entirely against the bundled sample parquet, so the suite is fast,
deterministic and works with no network access — which is what lets CI gate
every pull request.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pipeline import ingest, warehouse
from pipeline.config import FIXTURE_PATH, Config, load_config


@pytest.fixture(scope="session")
def cfg(tmp_path_factory: pytest.TempPathFactory) -> Config:
    """Project config pointed at a throwaway DuckDB file and raw-data directory.

    The raw layer must be isolated from the real ``data/raw/`` — otherwise
    whatever the developer last ingested there (live or fixture) leaks into
    the test suite, e.g. ``meta.is_sample`` in the published page.
    """
    base = load_config()
    scratch = tmp_path_factory.mktemp("fuel_watch")
    db_path = scratch / "test.duckdb"
    raw_dir = scratch / "raw"
    Config.raw_source_dir = property(lambda self: raw_dir / self.source_name)
    return replace(base, warehouse={**base.warehouse, "database": str(db_path)})


@pytest.fixture(scope="session")
def built_warehouse(cfg: Config) -> Config:
    """A fully built warehouse, ingested from the fixture into the isolated raw layer."""
    if not FIXTURE_PATH.exists():
        pytest.skip("Sample fixture missing — run `python scripts/make_fixture.py`.")
    snapshot = ingest.ingest(cfg, offline=True)
    warehouse.build(cfg, snapshot)
    return cfg
