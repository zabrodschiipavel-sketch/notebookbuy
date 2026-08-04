"""Tests for the Gemini deal-review wrapper (no network)."""
import json

from ai_service import AIService
from app_config import (
    GEMINI_MAX_RETRIES,
    GEMINI_PRO_MODEL,
    GEMINI_REVIEW_FALLBACK_MODELS,
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
            if model == GEMINI_PRO_MODEL:
                raise RuntimeError("429 RESOURCE_EXHAUSTED (free tier)")
            return _Resp(json.dumps({
                "reviews": [{"id": "1", "verdict": "exclude", "reason": "скам"}]
            }))

    svc = _service_with_models(Models())
    out = svc.review_deals([{"id": 1, "title": "Maci Brook pro", "price": 600}])

    assert out == {"1": {"verdict": "exclude", "reason": "скам"}}
    # Non-final models get a single fast attempt before the chain moves on.
    assert calls.count(GEMINI_PRO_MODEL) == 1
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
    assert calls.count(GEMINI_PRO_MODEL) == 1


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
