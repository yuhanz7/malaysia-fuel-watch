"""Reporting step: write a self-contained Markdown report from the marts.

The report is regenerated on every run and committed by CI, so the repository
always shows current numbers without anyone opening a notebook.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline.config import DOCS_DIR, Config
from pipeline.ingest import MANIFEST_NAME
from pipeline.quality import CheckResult
from pipeline.warehouse import query

log = logging.getLogger(__name__)

REPORT_PATH = DOCS_DIR / "REPORT.md"


def _md_table(frame: pd.DataFrame, headers: dict[str, str]) -> str:
    """Render a DataFrame as a Markdown table (no external dependency)."""
    frame = frame[list(headers)].rename(columns=headers)
    lines = [
        "| " + " | ".join(frame.columns) + " |",
        "| " + " | ".join("---" for _ in frame.columns) + " |",
    ]
    for _, row in frame.iterrows():
        cells = ["" if pd.isna(v) else str(v) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _fmt_rm(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"RM{float(value):.2f}"


def _fmt_signed(value: float | None, unit: str = "") -> str:
    if value is None or pd.isna(value):
        return "n/a"
    value = float(value)
    if abs(value) < 1e-9:
        return "no change"
    return f"{value:+.2f}{unit}"


def _headline(summary: pd.DataFrame) -> list[str]:
    """A few sentences written straight from the numbers."""
    lines: list[str] = []
    latest_date = pd.to_datetime(summary["latest_price_date"]).max().date()
    lines.append(f"Latest published week: **{latest_date:%d %B %Y}**.")

    movers = summary[summary["latest_change_rm"].abs() > 0]
    if movers.empty:
        lines.append("No grade changed price this week.")
    else:
        parts = [
            f"{row.fuel_type.upper()} {_fmt_signed(row.latest_change_rm)} to "
            f"{_fmt_rm(row.latest_price_rm)}"
            for row in movers.itertuples()
        ]
        lines.append("This week: " + "; ".join(parts) + ".")

    above = summary[summary["pct_vs_52w_avg"] > 0]
    below = summary[summary["pct_vs_52w_avg"] < 0]
    lines.append(
        f"{len(above)} of {len(summary)} grades sit above their own 52-week average, "
        f"{len(below)} below."
    )

    steadiest = summary.sort_values("pct_weeks_price_moved").iloc[0]
    lines.append(
        f"{steadiest.fuel_type.upper()} is the least volatile grade on record — its price "
        f"moved in only {steadiest.pct_weeks_price_moved:.0f}% of tracked weeks."
    )
    return lines


def _coverage(cfg: Config) -> str:
    manifest_path = cfg.raw_source_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return ""
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not entries:
        return ""
    latest = entries[-1]
    return (
        f"- Snapshots landed: **{len(entries)}**\n"
        f"- Latest snapshot: `{latest['snapshot_date']}` "
        f"({latest['rows']:,} source rows, {latest['min_date']} to {latest['max_date']})\n"
        f"- Source fingerprint: `{latest['sha256'][:16]}…`\n"
    )


def _fixture_banner(cfg: Config) -> str:
    """Warn loudly if this report was built from the synthetic sample data."""
    manifest_path = cfg.raw_source_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return ""
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if entries and str(entries[-1].get("origin", "")).startswith("fixture:"):
        return (
            "> **⚠️ Sample data.** This report was generated in offline mode from the "
            "synthetic fixture in `tests/fixtures/`, not from the live source. "
            "Run `make run` to rebuild it from data.gov.my.\n"
        )
    return ""


def _quality_section(results: list[CheckResult]) -> str:
    frame = pd.DataFrame(
        [
            {
                "check": r.name,
                "severity": r.severity,
                "result": "pass" if r.passed else f"fail ({r.failing_rows:,} rows)",
                "description": r.description,
            }
            for r in results
        ]
    )
    passed = sum(r.passed for r in results)
    header = f"{passed} of {len(results)} checks passed.\n\n"
    return header + _md_table(
        frame,
        {
            "check": "Check",
            "severity": "Severity",
            "result": "Result",
            "description": "What it guarantees",
        },
    )


def generate(cfg: Config, quality_results: list[CheckResult]) -> Path:
    """Write docs/REPORT.md and return its path."""
    summary = query(cfg, "SELECT * FROM mart_fuel_price_summary ORDER BY fuel_type")
    annual = query(
        cfg,
        """
        SELECT * FROM mart_annual_stats
        WHERE price_year >= (SELECT MAX(price_year) - 4 FROM mart_annual_stats)
        ORDER BY price_year DESC, fuel_type
        """,
    )
    moves = query(
        cfg,
        "SELECT * FROM mart_biggest_moves WHERE move_rank <= 3 ORDER BY fuel_type, move_rank",
    )

    display_summary = pd.DataFrame(
        {
            "fuel_type": summary["fuel_type"].str.upper(),
            "latest_price_rm": summary["latest_price_rm"].map(_fmt_rm),
            "latest_change_rm": summary["latest_change_rm"].map(_fmt_signed),
            "range_52w": [
                f"{_fmt_rm(lo)} – {_fmt_rm(hi)}"
                for lo, hi in zip(summary["low_52w_rm"], summary["high_52w_rm"])
            ],
            "pct_vs_52w_avg": summary["pct_vs_52w_avg"].map(lambda v: _fmt_signed(v, "%")),
            "yoy_change_pct": summary["yoy_change_pct"].map(lambda v: _fmt_signed(v, "%")),
            "weeks_tracked": summary["weeks_tracked"].astype("Int64"),
        }
    )

    display_annual = pd.DataFrame(
        {
            "price_year": annual["price_year"],
            "fuel_type": annual["fuel_type"].str.upper(),
            "avg_price_rm": annual["avg_price_rm"].map(_fmt_rm),
            "range": [
                f"{_fmt_rm(lo)} – {_fmt_rm(hi)}"
                for lo, hi in zip(annual["min_price_rm"], annual["max_price_rm"])
            ],
            "volatility_rm": annual["volatility_rm"].map(
                lambda v: "n/a" if pd.isna(v) else f"{float(v):.3f}"
            ),
            "net_change_pct": annual["net_change_pct"].map(lambda v: _fmt_signed(v, "%")),
            "weeks_up": annual["weeks_up"].astype("Int64"),
            "weeks_down": annual["weeks_down"].astype("Int64"),
            "weeks_flat": annual["weeks_flat"].astype("Int64"),
        }
    )

    display_moves = pd.DataFrame(
        {
            "fuel_type": moves["fuel_type"].str.upper(),
            "price_date": pd.to_datetime(moves["price_date"]).dt.strftime("%d %b %Y"),
            "change_rm": moves["change_rm"].map(_fmt_signed),
            "change_pct": moves["change_pct"].map(lambda v: _fmt_signed(v, "%")),
            "price_rm": moves["price_rm"].map(_fmt_rm),
        }
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    source = cfg.source

    content = f"""# Malaysia Fuel Watch — automated report

_Generated {generated_at} by the pipeline in this repository. Do not edit by hand._

{_fixture_banner(cfg)}
**Source:** [{source['publisher']}]({source['catalogue_page']}) · `{source['url']}`
**Licence:** {source['licence']} · **Cadence:** {source['update_cadence']}

## Headline

{chr(10).join(f"- {line}" for line in _headline(summary))}

## Where prices stand

{_md_table(display_summary, {
    "fuel_type": "Grade",
    "latest_price_rm": "Latest",
    "latest_change_rm": "Week on week",
    "range_52w": "52-week range",
    "pct_vs_52w_avg": "vs 52-week avg",
    "yoy_change_pct": "Year on year",
    "weeks_tracked": "Weeks tracked",
})}

![Latest price within the 52-week range](charts/current_vs_52w_range.png)

## Price history

![Weekly retail fuel prices](charts/price_history.png)

## Year by year

{_md_table(display_annual, {
    "price_year": "Year",
    "fuel_type": "Grade",
    "avg_price_rm": "Average",
    "range": "Range",
    "volatility_rm": "Volatility (σ)",
    "net_change_pct": "Net change",
    "weeks_up": "Weeks up",
    "weeks_down": "Weeks down",
    "weeks_flat": "Weeks flat",
})}

![Average price by year](charts/annual_average.png)

![Volatility by year](charts/annual_volatility.png)

## Largest weekly moves on record

{_md_table(display_moves, {
    "fuel_type": "Grade",
    "price_date": "Week",
    "change_rm": "Move",
    "change_pct": "Move %",
    "price_rm": "Price after",
})}

## Data quality

{_quality_section(quality_results)}

## Ingestion log

{_coverage(cfg)}
"""

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(content, encoding="utf-8")
    log.info("Wrote %s", REPORT_PATH)
    return REPORT_PATH
