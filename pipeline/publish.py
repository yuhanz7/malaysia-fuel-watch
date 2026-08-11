"""Publish step: render a static dashboard to docs/index.html.

The page is fully self-contained — data is embedded as JSON, the chart is drawn
with hand-written SVG, and there are no external scripts or fonts beyond one
Google Fonts stylesheet. That means it works on GitHub Pages with no build
step and no framework to keep up to date.
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

PAGE_PATH = DOCS_DIR / "index.html"

# Nozzle-colour conventions at a Malaysian pump: RON95 amber, RON97 red,
# diesel slate. Any other grade gets the next colour from the fallback list.
GRADE_COLOURS = {
    "ron95": "#C9922A",
    "ron97": "#B4342B",
    "diesel": "#2F5D6B",
}
FALLBACK_COLOURS = [
    "#5B6E4F", "#7A4E7E", "#A0522D", "#3F5B8B",
    "#4A7856", "#8B5FBF", "#6B4226", "#2F6E6E",
]


def _colour_map(grades: list[str]) -> dict[str, str]:
    """Known nozzle colours first; everything else gets its own fallback colour.

    The fallback index only advances for grades that actually use it, so a mix
    of known and unknown grades never collides (indexing by position in
    ``grades`` did, once there were more unknown grades than fallback colours).
    """
    colours: dict[str, str] = {}
    next_fallback = 0
    for grade in grades:
        known = GRADE_COLOURS.get(grade.lower())
        if known is not None:
            colours[grade] = known
        else:
            colours[grade] = FALLBACK_COLOURS[next_fallback % len(FALLBACK_COLOURS)]
            next_fallback += 1
    return colours


def _payload(cfg: Config, quality_results: list[CheckResult]) -> dict:
    """Everything the page needs, as plain JSON-serialisable structures."""
    fact = query(
        cfg,
        """
        SELECT price_date, fuel_type, price_rm
        FROM fct_fuel_price_weekly
        ORDER BY price_date
        """,
    )
    wide = fact.pivot(index="price_date", columns="fuel_type", values="price_rm").sort_index()
    grades = list(wide.columns)
    colours = _colour_map(grades)

    summary = query(cfg, "SELECT * FROM mart_fuel_price_summary ORDER BY fuel_type")
    annual = query(
        cfg,
        """
        SELECT * FROM mart_annual_stats
        WHERE price_year >= (SELECT MAX(price_year) - 5 FROM mart_annual_stats)
        ORDER BY price_year DESC, fuel_type
        """,
    )
    moves = query(
        cfg,
        "SELECT * FROM mart_biggest_moves WHERE move_rank <= 3 ORDER BY abs(change_rm) DESC",
    )

    def _num(value) -> float | None:
        return None if pd.isna(value) else float(value)

    def _iso(value) -> str:
        """Dates reach the page as plain YYYY-MM-DD, never as timestamps."""
        return pd.Timestamp(value).strftime("%Y-%m-%d")

    manifest_path = cfg.raw_source_dir / MANIFEST_NAME
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    )
    latest_snapshot = manifest[-1] if manifest else {}

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC"),
            "publisher": cfg.source["publisher"],
            "catalogue_page": cfg.source["catalogue_page"],
            "source_url": cfg.source["url"],
            "licence": cfg.source["licence"],
            "cadence": cfg.source["update_cadence"],
            "snapshots": len(manifest),
            "is_sample": str(latest_snapshot.get("origin", "")).startswith("fixture:"),
        },
        "grades": [
            {
                "key": grade,
                "label": grade.upper(),
                "colour": colours[grade],
            }
            for grade in grades
        ],
        "series": {
            "dates": [_iso(d) for d in wide.index],
            "values": {
                grade: [_num(v) for v in wide[grade].tolist()] for grade in grades
            },
        },
        "summary": [
            {
                "grade": row.fuel_type,
                "label": row.fuel_type.upper(),
                "colour": colours[row.fuel_type],
                "price": _num(row.latest_price_rm),
                "date": _iso(row.latest_price_date),
                "change": _num(row.latest_change_rm),
                "change_pct": _num(row.latest_change_pct),
                "low_52w": _num(row.low_52w_rm),
                "high_52w": _num(row.high_52w_rm),
                "avg_52w": _num(row.avg_52w_rm),
                "vs_avg_pct": _num(row.pct_vs_52w_avg),
                "yoy_pct": _num(row.yoy_change_pct),
                "weeks_tracked": int(row.weeks_tracked),
                "pct_weeks_moved": _num(row.pct_weeks_price_moved),
            }
            for row in summary.itertuples()
        ],
        "annual": [
            {
                "year": int(row.price_year),
                "grade": row.fuel_type.upper(),
                "avg": _num(row.avg_price_rm),
                "min": _num(row.min_price_rm),
                "max": _num(row.max_price_rm),
                "volatility": _num(row.volatility_rm),
                "net_pct": _num(row.net_change_pct),
                "up": int(row.weeks_up),
                "down": int(row.weeks_down),
                "flat": int(row.weeks_flat),
            }
            for row in annual.itertuples()
        ],
        "moves": [
            {
                "grade": row.fuel_type.upper(),
                "date": _iso(row.price_date),
                "change": _num(row.change_rm),
                "change_pct": _num(row.change_pct),
                "price": _num(row.price_rm),
            }
            for row in moves.itertuples()
        ],
        "quality": [
            {
                "name": r.name,
                "severity": r.severity,
                "passed": r.passed,
                "failing_rows": r.failing_rows,
                "description": r.description,
            }
            for r in quality_results
        ],
    }


def generate(cfg: Config, quality_results: list[CheckResult]) -> Path:
    """Write docs/index.html and return its path."""
    template = (Path(__file__).parent / "templates" / "dashboard.html").read_text(
        encoding="utf-8"
    )
    payload = _payload(cfg, quality_results)
    page = template.replace("/*__PAYLOAD__*/null", json.dumps(payload, separators=(",", ":")))

    PAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PAGE_PATH.write_text(page, encoding="utf-8")
    log.info("Wrote %s", PAGE_PATH)
    return PAGE_PATH
