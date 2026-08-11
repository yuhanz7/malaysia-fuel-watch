"""Tests for chart colour assignment."""

from __future__ import annotations

from pipeline.analyze import _colour_map

# The live source currently publishes 8 grades; a collision here means two
# unrelated grades render in the same colour across every chart.
LIVE_GRADE_COUNT_TODAY = [
    "diesel", "diesel_budi", "diesel_eastmsia", "diesel_skds",
    "ron95", "ron95_budi95", "ron95_skps", "ron97",
]


def test_colour_map_gives_every_grade_a_distinct_colour() -> None:
    colours = _colour_map(LIVE_GRADE_COUNT_TODAY)
    assert len(set(colours.values())) == len(LIVE_GRADE_COUNT_TODAY)
