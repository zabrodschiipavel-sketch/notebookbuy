"""Tests for the Gemini deal-review wrapper (no network)."""
import json

import ai_service
import retry_utils
from ai_service import AIService
from app_config import (
    GEMINI_MAX_RETRIES,
    GEMINI_REVIEW_FALLBACK_MODELS,
    GEMINI_REVIEW_MODEL,
)


class _Resp:
    def __init__(self, text: str):
        self.text = text


def _service_with_models(models) -> AIService:
    svc = AIService()
    svc.client = type("FakeClient", (), {"models": models})()
    return svc


def test_review_returns_empty_without_client():
    svc = AIService()
    svc.client = None  # explicit: a local .env may have configured a real key
    assert svc.review_deals([{"id": 1, "title": "x", "price": 1}]) == {}


def test_review_falls_back_when_pro_unavailable():
    calls = []

    class Models:
        @staticmethod
        def generate_content(model, contents, config):
            calls.append(model)
            if model == GEMINI_REVIEW_MODEL:
                raise RuntimeError("429 RESOURCE_EXHAUSTED (free tier)")
            return _Resp(json.dumps({
                "reviews": [{"id": "1", "verdict": "exclude", "reason": "скам"}]
            }))

    svc = _service_with_models(Models())
    out = svc.review_deals([{"id": 1, "title": "Maci Brook pro", "price": 600}])

    assert out == {"1": {"verdict": "exclude", "reason": "скам"}}
    # Non-final models get a single fast attempt before the chain moves on.
    assert calls.count(GEMINI_REVIEW_MODEL) == 1
    assert calls[-1] == GEMINI_REVIEW_FALLBACK_MODELS[0]


def test_review_returns_empty_when_all_models_fail(monkeypatch):
    monkeypatch.setattr("retry_utils.time.sleep", lambda s: None)
    calls = []

    class Models:
        @staticmethod
        def generate_content(model, contents, config):
            calls.append(model)
            raise RuntimeError("503 UNAVAILABLE")

    svc = _service_with_models(Models())
    assert svc.review_deals([{"id": 1, "title": "x", "price": 1}]) == {}
    # Last model in the chain retries patiently; the others bail after one try.
    assert calls.count(GEMINI_REVIEW_FALLBACK_MODELS[-1]) == GEMINI_MAX_RETRIES
    assert calls.count(GEMINI_REVIEW_MODEL) == 1


def test_review_prompt_keeps_the_intel_era_carve_out():
    """The impossible-configuration rule must keep its exception.

    Told only that a 128GB MacBook Air is fake, the model starts rejecting
    genuine pre-2018 Intel Airs, which really did ship with 128GB. Measured:
    with the carve-out both flash models scored 4/4 on impossible configs and
    0/6 false rejections; the rule alone is not safe to ship without it.
    """
    prompt = AIService._REVIEW_SYSTEM_PROMPT
    assert "256GB" in prompt, "the Apple silicon storage floor is the rule itself"
    assert "Intel MacBook Air" in prompt and "must NOT be rejected" in prompt
    assert "treat the configuration as plausible" in prompt, (
        "without the when-unsure clause the verdict starts deleting listings on a guess"
    )


def test_limiter_is_shared_per_model_not_per_call():
    """One budget per model: Gemini's RPM quota is per-model, and every caller
    of that model has to draw from the same bucket or throttling is pointless."""
    svc = AIService()
    assert svc.limiter_for("model-a") is svc.limiter_for("model-a")
    assert svc.limiter_for("model-a") is not svc.limiter_for("model-b")


def test_extract_specs_reports_failures(monkeypatch, caplog):
    monkeypatch.setattr("retry_utils.time.sleep", lambda s: None)
    # Otherwise this deliberate failure posts a real annotation on the CI run.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    class Models:
        @staticmethod
        def generate_content(model, contents, config):
            if "good" in contents[0]:
                return _Resp(json.dumps(
                    {"cpu": "i5", "gpu": "integrated", "ram": 8, "ssd": 256, "is_broken": False}
                ))
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    svc = _service_with_models(Models())
    ads = [
        {"id": "1", "title": "good one", "text": "t"},
        {"id": "2", "title": "bad one", "text": "t"},
    ]
    with caplog.at_level("INFO"):
        out = svc.extract_specs(ads)

    assert [r["id"] for r in out] == ["1"]
    assert "1/2 ads parsed, 1 failed" in caplog.text


def test_world_price_lookup_reads_brave_snippets(monkeypatch):
    """The model must extract from real results, not recall a price."""
    seen = {}

    class Models:
        @staticmethod
        def generate_content(model, contents, config):
            seen["prompt"] = contents
            return _Resp(json.dumps({"launch_usd": 1299, "current_usd": 899}))

    monkeypatch.setattr(
        ai_service.web_search, "search",
        lambda q, count=5: [{"title": "Zenbook 14 deal", "url": "https://shop/x",
                             "description": "now $899, was $1299"}],
    )
    svc = _service_with_models(Models())
    out = svc.lookup_world_price("Asus Zenbook 14", "Ryzen 7 8845HS", "integrated")

    assert out == {"launch_usd": 1299, "current_usd": 899}
    assert "$899" in seen["prompt"], "the snippet has to reach the model"


def test_lookups_return_empty_without_search_results(monkeypatch):
    """No Brave key or a failed search means no lookup at all — the caller then
    falls back to the component estimate, which the digest marks as one."""
    called = []

    class Models:
        @staticmethod
        def generate_content(model, contents, config):
            called.append(1)
            return _Resp("{}")

    monkeypatch.setattr(ai_service.web_search, "search", lambda q, count=5: [])
    svc = _service_with_models(Models())

    assert svc.lookup_world_price("X", "cpu", "gpu") == {}
    assert svc.lookup_nbc_score("X", "cpu", "gpu") == {}
    assert called == [], "no snippets means the model is never asked"


def test_nbc_lookup_survives_a_model_failure(monkeypatch):
    class Models:
        @staticmethod
        def generate_content(model, contents, config):
            raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setattr(retry_utils.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        ai_service.web_search, "search",
        lambda q, count=5: [{"title": "t", "url": "u", "description": "d"}],
    )
    assert _service_with_models(Models()).lookup_nbc_score("X", "cpu", "gpu") == {}


def test_review_explicit_model_skips_fallback(monkeypatch):
    monkeypatch.setattr("retry_utils.time.sleep", lambda s: None)
    calls = []

    class Models:
        @staticmethod
        def generate_content(model, contents, config):
            calls.append(model)
            raise RuntimeError("boom")

    svc = _service_with_models(Models())
    assert svc.review_deals([{"id": 1, "title": "x", "price": 1}], model="my-model") == {}
    assert set(calls) == {"my-model"}
