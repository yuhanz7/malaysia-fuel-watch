"""Analysis step: turn the marts into charts that go in the README/report.

Charts are written to ``docs/charts/`` as PNGs and regenerated on every run,
so the images in the repository always match the data in the warehouse.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this runs in CI

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from pipeline.config import CHART_DIR, Config
from pipeline.warehouse import query

log = logging.getLogger(__name__)

# A fixed palette keeps a grade the same colour in every chart. Sized with
# headroom above the 8 grades data.gov.my currently publishes so a new grade
# doesn't silently collide with an existing one.
PALETTE = [
    "#1b6ca8", "#e07a3c", "#3c8d5b", "#8d5ba6", "#c2456b", "#5c6b73",
    "#b8860b", "#2f8f8f", "#7a2e2e", "#4a4e9c",
]
GRID_KWARGS = {"color": "#d9d9d9", "linewidth": 0.7, "alpha": 0.8}


def _style_axes(ax: plt.Axes) -> None:
    ax.grid(axis="y", **GRID_KWARGS)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#9a9a9a")
    ax.spines["bottom"].set_color("#9a9a9a")


def _colour_map(fuel_types: list[str]) -> dict[str, str]:
    return {fuel: PALETTE[i % len(PALETTE)] for i, fuel in enumerate(sorted(fuel_types))}


def _save(fig: plt.Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("Wrote %s", path)
    return path


def chart_price_history(cfg: Config) -> Path:
    """Full weekly price history for every grade."""
    data = query(
        cfg,
        "SELECT price_date, fuel_type, price_rm FROM fct_fuel_price_weekly ORDER BY price_date",
    )
    colours = _colour_map(data["fuel_type"].unique().tolist())

    fig, ax = plt.subplots(figsize=(11, 5.5))
    for fuel, group in data.groupby("fuel_type"):
        ax.plot(
            group["price_date"],
            group["price_rm"],
            label=fuel.upper(),
            color=colours[fuel],
            linewidth=1.8,
        )
    ax.set_title("Malaysia weekly retail fuel prices", fontsize=14, fontweight="bold", pad=14)
    ax.set_ylabel("RM per litre")
    ax.set_xlabel("")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
    ax.legend(frameon=False, ncols=len(colours), loc="upper left")
    _style_axes(ax)
    return _save(fig, CHART_DIR / "price_history.png")


def chart_annual_average(cfg: Config) -> Path:
    """Average price per calendar year, per grade."""
    data = query(
        cfg,
        """
        SELECT price_year, fuel_type, avg_price_rm
        FROM mart_annual_stats
        ORDER BY price_year, fuel_type
        """,
    )
    pivot = data.pivot(index="price_year", columns="fuel_type", values="avg_price_rm")
    colours = _colour_map(list(pivot.columns))

    fig, ax = plt.subplots(figsize=(11, 5))
    pivot.plot(
        kind="bar",
        ax=ax,
        color=[colours[c] for c in pivot.columns],
        width=0.8,
        edgecolor="none",
    )
    ax.set_title("Average price by year", fontsize=14, fontweight="bold", pad=14)
    ax.set_ylabel("RM per litre")
    ax.set_xlabel("")
    ax.legend(
        [c.upper() for c in pivot.columns],
        frameon=False,
        ncols=len(pivot.columns),
        loc="upper left",
    )
    ax.tick_params(axis="x", rotation=0)
    _style_axes(ax)
    return _save(fig, CHART_DIR / "annual_average.png")


def chart_volatility(cfg: Config) -> Path:
    """How much each grade moved within each year."""
    data = query(
        cfg,
        """
        SELECT price_year, fuel_type, volatility_rm
        FROM mart_annual_stats
        WHERE volatility_rm IS NOT NULL
        ORDER BY price_year
        """,
    )
    colours = _colour_map(data["fuel_type"].unique().tolist())

    fig, ax = plt.subplots(figsize=(11, 5))
    for fuel, group in data.groupby("fuel_type"):
        ax.plot(
            group["price_year"],
            group["volatility_rm"],
            marker="o",
            markersize=5,
            label=fuel.upper(),
            color=colours[fuel],
            linewidth=1.8,
        )
    ax.set_title(
        "Price volatility by year (standard deviation of weekly price)",
        fontsize=13,
        fontweight="bold",
        pad=14,
    )
    ax.set_ylabel("RM")
    ax.set_xlabel("")
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.legend(frameon=False, ncols=len(colours), loc="upper left")
    _style_axes(ax)
    return _save(fig, CHART_DIR / "annual_volatility.png")


def chart_current_vs_range(cfg: Config) -> Path:
    """Latest price plotted against each grade's own 52-week range."""
    data = query(
        cfg,
        """
        SELECT fuel_type, latest_price_rm, low_52w_rm, high_52w_rm, avg_52w_rm
        FROM mart_fuel_price_summary
        ORDER BY fuel_type
        """,
    )
    colours = _colour_map(data["fuel_type"].tolist())

    fig, ax = plt.subplots(figsize=(9, 0.85 * len(data) + 1.6))
    for i, row in data.iterrows():
        colour = colours[row["fuel_type"]]
        ax.plot(
            [row["low_52w_rm"], row["high_52w_rm"]],
            [i, i],
            color=colour,
            linewidth=6,
            alpha=0.28,
            solid_capstyle="round",
        )
        ax.scatter(row["avg_52w_rm"], i, color=colour, s=45, marker="|", linewidths=2)
        ax.scatter(row["latest_price_rm"], i, color=colour, s=110, zorder=3)
        ax.annotate(
            f"RM{row['latest_price_rm']:.2f}",
            (row["latest_price_rm"], i),
            textcoords="offset points",
            xytext=(0, 12),
            ha="center",
            fontsize=9,
            fontweight="bold",
            color=colour,
        )

    ax.set_yticks(range(len(data)), [f.upper() for f in data["fuel_type"]])
    ax.set_ylim(len(data) - 0.45, -0.55)  # first grade at the top, tight margins
    ax.set_title(
        "Latest price within the 52-week range",
        fontsize=13,
        fontweight="bold",
        pad=14,
    )
    ax.set_xlabel("RM per litre")
    ax.grid(axis="x", **GRID_KWARGS)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    return _save(fig, CHART_DIR / "current_vs_52w_range.png")


def run(cfg: Config) -> list[Path]:
    """Generate every chart. Returns the paths written."""
    return [
        chart_price_history(cfg),
        chart_annual_average(cfg),
        chart_volatility(cfg),
        chart_current_vs_range(cfg),
    ]
