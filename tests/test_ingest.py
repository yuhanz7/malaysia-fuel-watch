"""Tests for snapshotting and the manifest."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from pipeline import ingest
from pipeline.config import FIXTURE_PATH, Config


@pytest.fixture()
def isolated(cfg: Config, tmp_path, monkeypatch) -> Config:
    """Config whose raw layer lives in a temp directory."""
    scratch = replace(cfg, source={**cfg.source, "name": "fuel_price"})
    monkeypatch.setattr(
        type(scratch),
        "raw_source_dir",
        property(lambda self: tmp_path / "raw" / self.source_name),
    )
    return scratch


def test_snapshot_and_manifest_are_written(isolated: Config) -> None:
    path = ingest.ingest(isolated, offline=True)
    assert path.exists()
    assert f"snapshot_date={date.today().isoformat()}" in str(path)

    manifest = json.loads((isolated.raw_source_dir / ingest.MANIFEST_NAME).read_text())
    assert len(manifest) == 1
    entry = manifest[0]
    assert entry["rows"] > 0
    assert entry["min_date"] < entry["max_date"]
    assert len(entry["sha256"]) == 64


def test_reingest_is_idempotent(isolated: Config) -> None:
    first = ingest.ingest(isolated, offline=True)
    first_mtime = first.stat().st_mtime_ns
    second = ingest.ingest(isolated, offline=True)
    assert second == first
    assert second.stat().st_mtime_ns == first_mtime, "second run should not rewrite the snapshot"


def test_latest_snapshot_resolves_the_newest_partition(isolated: Config) -> None:
    written = ingest.ingest(isolated, offline=True)
    older = isolated.raw_source_dir / "snapshot_date=2000-01-01"
    older.mkdir(parents=True, exist_ok=True)
    (older / "fuel_price.parquet").write_bytes(b"")
    assert ingest.latest_snapshot(isolated) == written


def test_missing_snapshots_raise_a_clear_error(isolated: Config) -> None:
    with pytest.raises(FileNotFoundError, match="No snapshots"):
        ingest.latest_snapshot(isolated)


def test_offline_snapshot_is_not_reused_by_a_live_run(isolated: Config, monkeypatch) -> None:
    """A same-day fixture snapshot must not silently satisfy a subsequent live run."""
    ingest.ingest(isolated, offline=True)
    manifest = json.loads((isolated.raw_source_dir / ingest.MANIFEST_NAME).read_text())
    assert manifest[0]["origin"].startswith("fixture:")

    monkeypatch.setattr(ingest, "_download", lambda url: FIXTURE_PATH.read_bytes())
    ingest.ingest(isolated, offline=False)

    manifest = json.loads((isolated.raw_source_dir / ingest.MANIFEST_NAME).read_text())
    assert len(manifest) == 1, "the stale fixture snapshot should be replaced, not duplicated"
    assert manifest[0]["origin"] == isolated.source_url
