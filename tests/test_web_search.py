"""Tests for the Brave Search client (no network)."""
import json

import pytest

import retry_utils
import web_search


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Neutralise the 1 rps limiter and retry back-off so tests stay instant."""
    monkeypatch.setattr(web_search._limiter, "acquire", lambda: None)
    monkeypatch.setattr(retry_utils.time, "sleep", lambda _: None)


BRAVE_PAYLOAD = {
    "web": {
        "results": [
            {
                "title": "Asus Zenbook 14 UM3406HA Review",
                "url": "https://www.notebookcheck.net/asus-zenbook-14.html",
                "description": "x" * 500,
            },
            {"title": "Buy Asus Zenbook 14", "url": "https://example.com/z14"},
        ]
    }
}


def test_search_returns_nothing_without_a_key(monkeypatch):
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "")
    assert web_search.is_configured() is False
    assert web_search.search("anything") == []


def test_search_maps_results_and_clips_descriptions(monkeypatch):
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "k")
    monkeypatch.setattr(web_search, "_fetch", lambda url: BRAVE_PAYLOAD)

    out = web_search.search("asus zenbook 14 price")

    assert [r["url"] for r in out] == [
        "https://www.notebookcheck.net/asus-zenbook-14.html",
        "https://example.com/z14",
    ]
    assert len(out[0]["description"]) == 300
    assert out[1]["description"] == ""  # missing field must not become None


def test_search_swallows_transport_errors(monkeypatch):
    """A failed search degrades to the component estimate, it never breaks the run."""
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "k")
    monkeypatch.setattr(web_search, "_fetch", _raise)

    assert web_search.search("asus zenbook") == []


def _raise(url):
    raise OSError("connection reset")


def test_search_sends_key_and_query(monkeypatch):
    seen = {}
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "secret-token")

    def fake_fetch(url):
        seen["url"] = url
        return BRAVE_PAYLOAD

    monkeypatch.setattr(web_search, "_fetch", fake_fetch)
    web_search.search("dell xps 15 price", count=3, freshness="py")

    assert "q=dell+xps+15+price" in seen["url"]
    assert "count=3" in seen["url"]
    assert "freshness=py" in seen["url"]


def test_search_count_is_clamped_to_the_api_maximum(monkeypatch):
    seen = {}
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "k")
    monkeypatch.setattr(web_search, "_fetch", lambda url: seen.setdefault("url", url) and {} or {})
    web_search.search("q", count=99)
    assert "count=10" in seen["url"]


def test_search_strips_markup_and_entities(monkeypatch):
    """Brave wraps query terms in <strong> and escapes quotes."""
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "k")
    monkeypatch.setattr(web_search, "_fetch", lambda url: {"web": {"results": [{
        "title": "<strong>ASUS</strong> Zenbook 14&quot; OLED",
        "url": "https://amazon.com/x",
        "description": "Buy <strong>Zenbook</strong> for &#36;899 &amp; save",
    }]}})

    out = web_search.search("zenbook")

    assert out[0]["title"] == 'ASUS Zenbook 14" OLED'
    assert out[0]["description"] == "Buy Zenbook for $899 & save"


def test_as_context_flattens_results():
    ctx = web_search.as_context(
        [{"title": "T", "url": "U", "description": "D"}, {"title": "T2", "url": "U2",
                                                          "description": "D2"}],
        limit=1,
    )
    assert "T" in ctx and "U" in ctx and "D" in ctx
    assert "T2" not in ctx


def test_payload_shape_is_tolerated_when_web_key_missing(monkeypatch):
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "k")
    monkeypatch.setattr(web_search, "_fetch", lambda url: json.loads("{}"))
    assert web_search.search("q") == []
