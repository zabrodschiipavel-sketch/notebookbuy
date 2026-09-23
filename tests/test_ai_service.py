"""Tests for the AI layer (no network: the chat call is a fake)."""
import json

import ai_service
from ai_service import AIService, model_chain
from app_config import (
    AI_EXTRACT_BATCH_SIZE,
    OPENROUTER_FALLBACK_MODELS,
    OPENROUTER_MODEL,
    OPENROUTER_REVIEW_MODEL,
)


class FakeChat:
    """Records every call and answers with whatever *respond* returns."""

    def __init__(self, respond):
        self.respond = respond
        self.calls: list[dict] = []

    def __call__(self, system, user, schema, **kwargs):
        call = {"system": system, "user": user, "schema": schema, **kwargs}
        self.calls.append(call)
        return self.respond(call), kwargs["models"][0]


def _failing(call):
    raise RuntimeError("429 free-models-per-day")


def test_disabled_service_makes_no_calls():
    svc = AIService(chat=FakeChat(_failing))
    svc.enabled = False  # as when OPENROUTER_API_KEY is empty
    assert svc.review_deals([{"id": 1, "title": "x", "price": 1}]) == {}
    assert svc.extract_specs([{"id": "1", "title": "t", "text": "x"}]) == []
    assert svc.lookup_world_price("X", "cpu", "gpu") == {}


def test_model_chain_puts_primary_first_without_duplicates():
    chain = model_chain(OPENROUTER_MODEL)
    assert chain[0] == OPENROUTER_MODEL
    assert chain[1:] == [m for m in OPENROUTER_FALLBACK_MODELS if m != OPENROUTER_MODEL]
    assert model_chain(OPENROUTER_FALLBACK_MODELS[0])[0] == OPENROUTER_FALLBACK_MODELS[0]
    assert len(set(model_chain(OPENROUTER_FALLBACK_MODELS[0]))) == len(model_chain(OPENROUTER_FALLBACK_MODELS[0]))


def test_default_models_are_the_free_primary_and_muse_spark_fallback():
    """The primary must stay a :free model; the paid fallback only catches what it drops."""
    assert OPENROUTER_MODEL.endswith(":free")
    assert "meta/muse-spark-1.3-contributor" in OPENROUTER_FALLBACK_MODELS


def test_review_sends_the_whole_chain_and_parses_verdicts():
    chat = FakeChat(lambda call: {"reviews": [
        {"id": "1", "verdict": "exclude", "reason": "скам"},
        {"id": "2", "verdict": "GREAT", "reason": "норм"},
    ]})
    out = AIService(chat=chat).review_deals([
        {"id": 1, "title": "Maci Brook pro", "price": 600},
        {"id": 2, "title": "ThinkPad T14", "price": 7000},
    ])

    assert out == {
        "1": {"verdict": "exclude", "reason": "скам"},
        "2": {"verdict": "great", "reason": "норм"},
    }
    assert chat.calls[0]["models"] == model_chain(OPENROUTER_REVIEW_MODEL)
    assert "Maci Brook pro" in chat.calls[0]["user"]


def test_review_drops_verdicts_outside_the_enum():
    """A fallback that ignores the schema may say 'reject'. Guessing what it
    meant could delete a listing, so it stays unreviewed instead."""
    chat = FakeChat(lambda call: {"reviews": [{"id": "1", "verdict": "reject", "reason": "?"}]})
    assert AIService(chat=chat).review_deals([{"id": 1, "title": "x", "price": 1}]) == {}


def test_review_returns_empty_when_every_model_fails():
    assert AIService(chat=FakeChat(_failing)).review_deals([{"id": 1, "title": "x", "price": 1}]) == {}


def test_review_explicit_model_skips_fallback():
    chat = FakeChat(lambda call: {"reviews": []})
    AIService(chat=chat).review_deals([{"id": 1, "title": "x", "price": 1}], model="my-model")
    assert chat.calls[0]["models"] == ["my-model"]


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


def _ads(n):
    return [{"id": str(i), "title": f"ad {i}", "text": "i5 8gb"} for i in range(n)]


def test_extract_specs_batches_requests():
    """The free tier's binding limit is requests per day; ten ads must cost
    one request, not ten."""
    def respond(call):
        ids = [ad["id"] for ad in json.loads(call["user"].split("\n", 1)[1])]
        return {"laptops": [
            {"id": i, "cpu": "Intel Core i5-8250U", "gpu": "integrated", "ram": 8, "ssd": 256, "is_broken": False}
            for i in ids
        ]}

    chat = FakeChat(respond)
    n = AI_EXTRACT_BATCH_SIZE * 2 + 1
    out = AIService(chat=chat).extract_specs(_ads(n))

    assert len(chat.calls) == 3
    assert sorted(r["id"] for r in out) == sorted(str(i) for i in range(n))
    assert all(call["models"] == model_chain(OPENROUTER_MODEL) for call in chat.calls)


def test_extract_specs_rejects_unknown_ids_and_coerces_types():
    chat = FakeChat(lambda call: {"laptops": [
        {"id": "0", "cpu": "Ryzen 5 5500U", "gpu": "", "ram": "16GB", "ssd": "1 TB", "is_broken": "false"},
        {"id": "0", "cpu": "duplicate", "gpu": "x", "ram": 4, "ssd": 0, "is_broken": True},
        {"id": "999", "cpu": "hallucinated", "gpu": "x", "ram": 8, "ssd": 0, "is_broken": False},
    ]})
    out = AIService(chat=chat).extract_specs(_ads(1))

    assert out == [{
        "id": "0", "cpu": "Ryzen 5 5500U", "gpu": "integrated",
        "ram": 16, "ssd": 1024, "is_broken": False,
    }]


def test_extract_specs_reports_failures(monkeypatch, caplog):
    # Otherwise this deliberate failure posts a real annotation on the CI run.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    chat = FakeChat(lambda call: {"laptops": [
        {"id": "0", "cpu": "i5", "gpu": "integrated", "ram": 8, "ssd": 256, "is_broken": False},
    ]})
    with caplog.at_level("INFO"):
        out = AIService(chat=chat).extract_specs(_ads(2))

    assert [r["id"] for r in out] == ["0"]
    assert "1/2 ads parsed, 1 failed" in caplog.text


def test_extract_specs_survives_a_failed_batch(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert AIService(chat=FakeChat(_failing)).extract_specs(_ads(3)) == []


def test_world_price_lookup_reads_brave_snippets(monkeypatch):
    """The model must extract from real results, not recall a price."""
    chat = FakeChat(lambda call: {"launch_usd": 1299, "current_usd": "899"})
    monkeypatch.setattr(
        ai_service.web_search, "search",
        lambda q, count=5: [{"title": "Zenbook 14 deal", "url": "https://shop/x",
                             "description": "now $899, was $1299"}],
    )
    out = AIService(chat=chat).lookup_world_price("Asus Zenbook 14", "Ryzen 7 8845HS", "integrated")

    assert out == {"launch_usd": 1299.0, "current_usd": 899.0}, "a string price must arrive as a number"
    assert "$899" in chat.calls[0]["user"], "the snippet has to reach the model"


def test_lookup_that_found_nothing_is_a_miss(monkeypatch):
    """Zeros mean 'not found'. Returned as data they were cached as a hit
    forever and the lookup was never retried; empty, they expire as a miss."""
    monkeypatch.setattr(
        ai_service.web_search, "search",
        lambda q, count=5: [{"title": "t", "url": "u", "description": "d"}],
    )
    chat = FakeChat(lambda call: {"launch_usd": 0, "current_usd": 0, "score": 0, "url": ""})
    svc = AIService(chat=chat)

    assert svc.lookup_world_price("X", "cpu", "gpu") == {}
    assert svc.lookup_nbc_score("X", "cpu", "gpu") == {}


def test_lookups_return_empty_without_search_results(monkeypatch):
    """No Brave key or a failed search means no lookup at all — the caller then
    falls back to the component estimate, which the digest marks as one."""
    chat = FakeChat(lambda call: {})
    monkeypatch.setattr(ai_service.web_search, "search", lambda q, count=5: [])
    svc = AIService(chat=chat)

    assert svc.lookup_world_price("X", "cpu", "gpu") == {}
    assert svc.lookup_nbc_score("X", "cpu", "gpu") == {}
    assert chat.calls == [], "no snippets means the model is never asked"


def test_nbc_lookup_survives_a_model_failure(monkeypatch):
    monkeypatch.setattr(
        ai_service.web_search, "search",
        lambda q, count=5: [{"title": "t", "url": "u", "description": "d"}],
    )
    assert AIService(chat=FakeChat(_failing)).lookup_nbc_score("X", "cpu", "gpu") == {}
