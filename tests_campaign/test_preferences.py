"""Robins val i klippdata/preferences.json: hjälpen för inskick och landet för kampanjbetyget."""

from __future__ import annotations

import json

import pytest

from campaign import preferences


def _saved(settings) -> dict:
    return json.loads((settings.data_dir / "preferences.json").read_text(encoding="utf-8"))


def test_the_country_is_sweden_when_nothing_is_saved(settings) -> None:
    assert not (settings.data_dir / "preferences.json").exists()
    assert preferences.country(settings) == "SE"


def test_saving_the_country_keeps_the_helper_and_the_other_way_round(settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    preferences.save_submit_helper(settings, False)
    preferences.save_country(settings, "FR")
    assert _saved(settings) == {"submit_helper": False, "country": "FR"}
    preferences.save_submit_helper(settings, True)
    assert _saved(settings) == {"submit_helper": True, "country": "FR"}
    assert (preferences.country(settings), preferences.submit_helper(settings)) == ("FR", True)


@pytest.mark.parametrize("code", ["XX", "fr", "", "SWE"])
def test_a_country_outside_the_list_is_an_error(settings, code) -> None:
    with pytest.raises(ValueError):
        preferences.save_country(settings, code)
    assert preferences.country(settings) == "SE"


def test_a_saved_country_outside_the_list_counts_as_sweden(settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "preferences.json").write_text('{"country": "XX", "submit_helper": false}')
    assert (preferences.country(settings), preferences.submit_helper(settings)) == ("SE", False)


def test_the_list_has_swedish_names() -> None:
    assert list(preferences.COUNTRIES)[:3] == ["SE", "NO", "DK"]
    assert (preferences.COUNTRIES["SE"], preferences.COUNTRIES["FR"], len(preferences.COUNTRIES)) == (
        "Sverige", "Frankrike", 19)
