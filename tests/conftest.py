"""Shared fixtures: keep every test off the network and deterministic."""
import pytest

import currency


@pytest.fixture(autouse=True)
def _fixed_exchange_rates(monkeypatch):
    """Rates load lazily on first use; pin them so no test fetches them."""
    monkeypatch.setattr(currency, "_rates", (18.0, 19.5))
