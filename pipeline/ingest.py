"""Extract step: pull the source dataset and land an immutable snapshot.

Design notes
------------
* Snapshots are written to ``data/raw/<source>/snapshot_date=YYYY-MM-DD/`` so
  every run keeps its own copy. The published dataset is revised occasionally,
  and a partitioned raw layer means those revisions are auditable instead of
  silently overwritten.
* A manifest records row counts, a content hash and the fetch timestamp for
  each snapshot. This is what makes "did the data actually change this week?"
  answerable without diffing parquet files.
* The step is idempotent: re-running on the same day reuses the snapshot
  unless ``--force`` is passed.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from pipeline.config import Config, FIXTURE_PATH

log = logging.getLogger(__name__)

MANIFEST_NAME = "_manifest.json"
REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 3


def _download(url: str) -> bytes:
    """Download a URL with simple exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            log.info("Downloading %s (attempt %d/%d)", url, attempt, MAX_ATTEMPTS)
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:  # network flakes are expected
            last_error = exc
            wait = 2**attempt
            log.warning("Download failed (%s). Retrying in %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Could not download {url} after {MAX_ATTEMPTS} attempts") from last_error


def _read_source(cfg: Config, offline: bool) -> tuple[pd.DataFrame, str, bytes]:
    """Return (dataframe, origin label, raw bytes) for the configured source."""
    if offline:
        log.info("Offline mode: reading local fixture %s", FIXTURE_PATH)
        if not FIXTURE_PATH.exists():
            raise FileNotFoundError(
                f"Fixture {FIXTURE_PATH} is missing. Run `python scripts/make_fixture.py` first."
            )
        payload = FIXTURE_PATH.read_bytes()
        return pd.read_parquet(io.BytesIO(payload)), f"fixture:{FIXTURE_PATH.name}", payload

    payload = _download(cfg.source_url)
    return pd.read_parquet(io.BytesIO(payload)), cfg.source_url, payload


def _manifest_path(cfg: Config) -> Path:
    return cfg.raw_source_dir / MANIFEST_NAME


def _load_manifest(cfg: Config) -> list[dict]:
    path = _manifest_path(cfg)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(cfg: Config, entries: list[dict]) -> None:
    path = _manifest_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _is_fixture_snapshot(cfg: Config, snapshot_date: str) -> bool:
    """True if the manifest records today's snapshot as coming from the fixture."""
    for entry in _load_manifest(cfg):
        if entry["snapshot_date"] == snapshot_date:
            return str(entry["origin"]).startswith("fixture:")
    return False


def ingest(cfg: Config, offline: bool = False, force: bool = False) -> Path:
    """Fetch the source and land a dated snapshot. Returns the parquet path."""
    snapshot_date = date.today().isoformat()
    partition = cfg.raw_source_dir / f"snapshot_date={snapshot_date}"
    target = partition / f"{cfg.source_name}.parquet"

    # A fixture snapshot must never satisfy a live run: reusing it here would
    # silently publish sample data from a "live" invocation.
    stale_fixture = (not offline) and _is_fixture_snapshot(cfg, snapshot_date)

    if target.exists() and not force and not stale_fixture:
        log.info("Snapshot for %s already exists, skipping download.", snapshot_date)
        return target

    if stale_fixture:
        log.warning(
            "Snapshot for %s came from the offline fixture; re-fetching from the live source.",
            snapshot_date,
        )

    frame, origin, payload = _read_source(cfg, offline=offline)
    if frame.empty:
        raise ValueError("Source returned zero rows — refusing to overwrite the raw layer.")

    partition.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)

    date_col = cfg.date_column
    dates = pd.to_datetime(frame[date_col], errors="coerce")

    entry = {
        "snapshot_date": snapshot_date,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": origin,
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "min_date": str(dates.min().date()) if dates.notna().any() else None,
        "max_date": str(dates.max().date()) if dates.notna().any() else None,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

    entries = [e for e in _load_manifest(cfg) if e["snapshot_date"] != snapshot_date]
    entries.append(entry)
    entries.sort(key=lambda e: e["snapshot_date"])
    _write_manifest(cfg, entries)

    log.info(
        "Landed %s rows covering %s to %s at %s",
        entry["rows"],
        entry["min_date"],
        entry["max_date"],
        target,
    )
    return target


def latest_snapshot(cfg: Config) -> Path:
    """Path to the most recent snapshot parquet on disk."""
    partitions = sorted(cfg.raw_source_dir.glob("snapshot_date=*"))
    if not partitions:
        raise FileNotFoundError(
            f"No snapshots under {cfg.raw_source_dir}. Run the ingest step first."
        )
    files = sorted(partitions[-1].glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"Snapshot partition {partitions[-1]} contains no parquet file.")
    return files[0]
