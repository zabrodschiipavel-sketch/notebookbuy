"""
Exchange rates to MDL, fetched once per process and cached on disk for 24 h.

Rates load lazily on first use: importing this module used to fire a network
request, so every script — and every test — that merely imported the scraper
paid for it. One source of truth also matters: the world-price comparison
used a hardcoded 18.0 while listing prices were converted at the live rate.
"""
import json
import logging
import os
import threading
import time

import requests


log = logging.getLogger(__name__)

CACHE_FILE = "currency_cache.json"
CACHE_TTL_SEC = 86400  # 24 hours

# Fallback values
DEFAULT_USD_TO_MDL = 17.8
DEFAULT_EUR_TO_MDL = 19.3

_lock = threading.Lock()
_rates: tuple[float, float] | None = None


def _fetch_rates_from_api() -> dict:
    """Fetch USD-based rates from a free API."""
    url = "https://open.er-api.com/v6/latest/USD"
    log.info("Fetching exchange rates from open.er-api.com...")
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "success":
        raise RuntimeError("API returned failure status")
    return data["rates"]


def _read_cache(max_age_sec: float | None) -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        if max_age_sec is not None and time.time() - os.path.getmtime(CACHE_FILE) >= max_age_sec:
            return {}
        with open(CACHE_FILE, encoding="utf-8") as f:
            rates = json.load(f)
        return rates if "MDL" in rates and "EUR" in rates else {}
    except Exception as e:
        log.warning("Failed to read currency cache: %s", e)
        return {}


def load_rates() -> tuple[float, float]:
    """
    Load exchange rates from cache or API.
    Returns (USD_TO_MDL, EUR_TO_MDL).
    """
    rates = _read_cache(CACHE_TTL_SEC)
    if not rates:
        try:
            api_rates = _fetch_rates_from_api()
            rates = {"MDL": api_rates["MDL"], "EUR": api_rates["EUR"]}
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(rates, f, indent=2)
        except Exception as e:
            log.warning("Failed to fetch fresh rates, using fallback: %s", e)
            # A stale cache still beats the hardcoded defaults.
            rates = _read_cache(None) or {
                "MDL": DEFAULT_USD_TO_MDL, "EUR": DEFAULT_USD_TO_MDL / DEFAULT_EUR_TO_MDL,
            }

    # open.er-api.com returns rates relative to USD:
    # 1 USD = rates["MDL"] MDL, 1 USD = rates["EUR"] EUR → 1 EUR = MDL / EUR.
    usd_to_mdl = rates["MDL"]
    eur_to_mdl = rates["MDL"] / rates["EUR"] if rates.get("EUR") else DEFAULT_EUR_TO_MDL
    return usd_to_mdl, eur_to_mdl


def _loaded() -> tuple[float, float]:
    global _rates
    with _lock:
        if _rates is None:
            _rates = load_rates()
            log.info("Loaded exchange rates: USD/MDL = %.2f, EUR/MDL = %.2f", *_rates)
        return _rates


def usd_to_mdl() -> float:
    return _loaded()[0]


def eur_to_mdl() -> float:
    return _loaded()[1]


def __getattr__(name: str) -> float:
    # Keeps `from currency import USD_TO_MDL` working, loaded on first access.
    if name == "USD_TO_MDL":
        return usd_to_mdl()
    if name == "EUR_TO_MDL":
        return eur_to_mdl()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
