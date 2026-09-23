"""Tests for exchange rate loading and fallback behavior (no network)."""
import json

import pytest

import currency


def test_fallback_values_are_reasonable():
    assert 10 < currency.DEFAULT_USD_TO_MDL < 30, "USD/MDL fallback is out of range"
    assert 10 < currency.DEFAULT_EUR_TO_MDL < 35, "EUR/MDL fallback is out of range"


def test_module_attributes_resolve_lazily():
    """`from currency import USD_TO_MDL` keeps working and reads the loaded rate."""
    from currency import EUR_TO_MDL, USD_TO_MDL

    assert USD_TO_MDL == currency.usd_to_mdl() == 18.0
    assert EUR_TO_MDL == currency.eur_to_mdl() == 19.5
    with pytest.raises(AttributeError):
        currency.NOT_A_RATE  # noqa: B018


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(currency, "_rates", None)


def test_rates_come_from_the_api_and_are_cached(isolated, monkeypatch):
    calls = []

    def api():
        calls.append(1)
        return {"MDL": 17.0, "EUR": 0.85}

    monkeypatch.setattr(currency, "_fetch_rates_from_api", api)
    assert currency.usd_to_mdl() == 17.0
    assert currency.eur_to_mdl() == pytest.approx(20.0)
    assert currency.usd_to_mdl() == 17.0
    assert calls == [1], "one fetch per process"
    with open(currency.CACHE_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"MDL": 17.0, "EUR": 0.85}


def test_stale_cache_beats_the_defaults_when_offline(isolated, monkeypatch):
    with open(currency.CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"MDL": 16.5, "EUR": 0.9}, f)
    monkeypatch.setattr(currency, "CACHE_TTL_SEC", -1)  # everything is stale

    def offline():
        raise ConnectionError("offline")

    monkeypatch.setattr(currency, "_fetch_rates_from_api", offline)
    assert currency.usd_to_mdl() == 16.5


def test_defaults_when_offline_without_cache(isolated, monkeypatch):
    def offline():
        raise ConnectionError("offline")

    monkeypatch.setattr(currency, "_fetch_rates_from_api", offline)
    assert currency.usd_to_mdl() == currency.DEFAULT_USD_TO_MDL
    assert currency.eur_to_mdl() == pytest.approx(currency.DEFAULT_EUR_TO_MDL)
