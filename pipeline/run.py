"""Command line entry point.

    python -m pipeline.run all          # ingest -> build -> quality -> charts -> report
    python -m pipeline.run ingest       # just refresh the raw snapshot
    python -m pipeline.run build        # rebuild the warehouse from the last snapshot
    python -m pipeline.run quality      # re-run the data quality checks
    python -m pipeline.run report       # regenerate charts and the Markdown report

Add --offline to run the whole thing against the bundled sample fixture,
which is useful for tests and for working without a network connection.
"""

from __future__ import annotations

import argparse
import logging
import sys

from pipeline import analyze, ingest, publish, quality, report, warehouse
from pipeline.config import load_config

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s"


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Malaysia Fuel Watch data pipeline")
    parser.add_argument(
        "step",
        choices=["all", "ingest", "build", "quality", "report"],
        help="Which stage to run.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use the bundled sample fixture instead of downloading the source.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if today's snapshot already exists.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    log = logging.getLogger("pipeline")
    cfg = load_config()

    run_ingest = args.step in {"all", "ingest"}
    run_build = args.step in {"all", "build"}
    run_quality = args.step in {"all", "quality"}
    run_report = args.step in {"all", "report"}

    if run_ingest:
        log.info("STEP 1/4  Ingest")
        ingest.ingest(cfg, offline=args.offline, force=args.force)

    if run_build:
        log.info("STEP 2/4  Build warehouse")
        warehouse.build(cfg, ingest.latest_snapshot(cfg))

    results: list[quality.CheckResult] = []
    if run_quality:
        log.info("STEP 3/4  Data quality")
        results = quality.run_checks(cfg)
        if quality.has_blocking_failure(results) and cfg.quality.get("fail_on_error", True):
            log.error("Blocking data quality failure — stopping before publishing.")
            return 1

    if run_report:
        log.info("STEP 4/4  Charts, report and dashboard")
        if not results:
            results = quality.run_checks(cfg)
        analyze.run(cfg)
        report.generate(cfg, results)
        publish.generate(cfg, results)

    log.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
