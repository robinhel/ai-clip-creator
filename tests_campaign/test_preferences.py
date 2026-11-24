"""Robins val i klippdata/preferences.json: hjälpen för inskick och landet för kampanjbetyget."""

from __future__ import annotations

import json
import threading
import time

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
        "Sweden", "France", 19)


def test_two_saves_at_the_same_time_keep_both_values(settings, monkeypatch) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    read = preferences._read

    def slow_read(settings) -> dict:  # den andra sparningen läser medan den första ännu inte har skrivit
        saved = read(settings)
        time.sleep(0.05)
        return saved

    monkeypatch.setattr(preferences, "_read", slow_read)
    threads = [threading.Thread(target=preferences.save_country, args=(settings, "FR")),
               threading.Thread(target=preferences.save_submit_helper, args=(settings, False))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert _saved(settings) == {"country": "FR", "submit_helper": False}
