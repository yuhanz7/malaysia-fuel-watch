"""Tests for the static dashboard."""

from __future__ import annotations

import json
import re

import pytest

from pipeline import publish, quality
from pipeline.config import Config


@pytest.fixture(scope="module")
def page(built_warehouse: Config, tmp_path_factory: pytest.TempPathFactory) -> str:
    """Render the dashboard to a throwaway path — ``generate()`` must never
    overwrite the real ``docs/index.html``, which may hold a live build."""
    results = quality.run_checks(built_warehouse)
    target = tmp_path_factory.mktemp("publish") / "index.html"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(publish, "PAGE_PATH", target)
        return publish.generate(built_warehouse, results).read_text(encoding="utf-8")


def _payload(page: str) -> dict:
    match = re.search(r"const DATA = (.*?);\n", page, re.S)
    assert match, "the page must embed its data as JSON"
    return json.loads(match.group(1))


def test_page_embeds_valid_json(page: str) -> None:
    data = _payload(page)
    assert set(data) == {"meta", "grades", "series", "summary", "annual", "moves", "quality"}


def test_placeholder_is_fully_replaced(page: str) -> None:
    assert "__PAYLOAD__" not in page
    assert "const DATA = null" not in page


def test_series_and_summary_agree_on_grades(page: str) -> None:
    data = _payload(page)
    grade_keys = {g["key"] for g in data["grades"]}
    assert grade_keys == set(data["series"]["values"])
    assert grade_keys == {s["grade"] for s in data["summary"]}


def test_every_series_is_the_same_length_as_the_date_axis(page: str) -> None:
    data = _payload(page)
    expected = len(data["series"]["dates"])
    for grade, values in data["series"]["values"].items():
        assert len(values) == expected, f"{grade} is misaligned with the date axis"


def test_dates_are_plain_iso_days(page: str) -> None:
    """Timestamps would break date parsing in the browser."""
    data = _payload(page)
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    assert all(pattern.match(d) for d in data["series"]["dates"])
    assert all(pattern.match(s["date"]) for s in data["summary"])
    assert all(pattern.match(m["date"]) for m in data["moves"])


def test_sample_data_is_flagged_on_the_page(page: str) -> None:
    """Built from the fixture, so the page must say so."""
    assert _payload(page)["meta"]["is_sample"] is True


def test_quality_results_are_published(page: str) -> None:
    checks = _payload(page)["quality"]
    assert len(checks) == 8
    assert all({"name", "severity", "passed", "description"} <= set(c) for c in checks)


def test_colour_map_gives_every_grade_a_distinct_colour() -> None:
    """More unknown grades than fixed nozzle colours must not collide on the fallback list."""
    grades = [
        "diesel", "diesel_budi", "diesel_eastmsia", "diesel_skds",
        "ron95", "ron95_budi95", "ron95_skps", "ron97",
    ]
    colours = publish._colour_map(grades)
    assert len(set(colours.values())) == len(grades)
