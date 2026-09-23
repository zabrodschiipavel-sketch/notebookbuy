"""Tests for the OpenRouter client (fake transport, no network)."""
import json

import pytest

import openrouter
import retry_utils
from openrouter import OpenRouterError, chat_json, parse_json_payload


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, text=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(body or {})

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


def _answer(content, model="nvidia/nemotron-3-super-120b-a12b:free"):
    return FakeResponse(body={"model": model, "choices": [{"message": {"content": content}}]})


@pytest.fixture
def transport(monkeypatch):
    """Queue responses; every request payload is recorded."""
    monkeypatch.setattr(retry_utils.time, "sleep", lambda s: None)
    monkeypatch.setattr(openrouter._limiter, "acquire", lambda: None)
    sent: list[dict] = []
    queue: list[FakeResponse] = []

    def post(payload):
        sent.append(payload)
        return queue.pop(0)

    monkeypatch.setattr(openrouter, "_post", post)
    return sent, queue


SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def test_request_carries_fallbacks_schema_and_healing(transport):
    sent, queue = transport
    queue.append(_answer('{"x": 1}'))

    data, served = chat_json("sys", "user", SCHEMA, models=["a:free", "b", "c"], label="t",
                             reasoning_effort="low")

    assert data == {"x": 1}
    payload = sent[0]
    assert payload["model"] == "a:free"
    assert payload["models"] == ["b", "c"], "fallbacks go server-side, after the primary"
    assert payload["response_format"]["json_schema"]["schema"] == SCHEMA
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert {"id": "response-healing"} in payload["plugins"]
    assert payload["reasoning"] == {"effort": "low", "exclude": True}
    assert '"x"' in payload["messages"][0]["content"], (
        "the schema must be in the prompt too, for a fallback without structured outputs"
    )


def test_single_model_sends_no_fallback_list(transport):
    sent, queue = transport
    queue.append(_answer('{"x": 1}'))
    chat_json("s", "u", SCHEMA, models=["only"], label="t")
    assert "models" not in sent[0]
    assert "reasoning" not in sent[0]


def test_reports_the_model_that_answered(transport, caplog):
    _, queue = transport
    queue.append(_answer('{"x": 2}', model="meta/muse-spark-1.3-contributor"))
    with caplog.at_level("WARNING"):
        _, served = chat_json("s", "u", SCHEMA,
                              models=["nvidia/nemotron-3-super-120b-a12b:free",
                                      "meta/muse-spark-1.3-contributor"], label="t")
    assert served == "meta/muse-spark-1.3-contributor"
    assert "answered by fallback" in caplog.text


def test_free_suffix_is_not_mistaken_for_a_fallback(transport, caplog):
    _, queue = transport
    queue.append(_answer('{"x": 2}', model="nvidia/nemotron-3-super-120b-a12b"))
    with caplog.at_level("WARNING"):
        chat_json("s", "u", SCHEMA, models=["nvidia/nemotron-3-super-120b-a12b:free"], label="t")
    assert "fallback" not in caplog.text


def test_rate_limit_is_retried_with_retry_after(transport, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(retry_utils.time, "sleep", slept.append)
    sent, queue = transport
    queue.append(FakeResponse(429, {"error": {"code": 429, "message": "Rate limit exceeded"}},
                              headers={"Retry-After": "7"}))
    queue.append(_answer('{"x": 3}'))

    data, _ = chat_json("s", "u", SCHEMA, models=["a"], label="t")

    assert data == {"x": 3}
    assert len(sent) == 2
    assert slept and slept[0] >= 7.0, "the server's Retry-After must be honoured"


@pytest.mark.parametrize("status", [400, 401, 402, 404])
def test_permanent_errors_are_not_retried(transport, status):
    sent, queue = transport
    queue.append(FakeResponse(status, {"error": {"code": status, "message": "nope"}}))
    with pytest.raises(OpenRouterError) as err:
        chat_json("s", "u", SCHEMA, models=["a"], label="t", max_retries=3)
    assert err.value.status == status
    assert len(sent) == 1, "retrying a bad key or an empty balance only burns quota"


def test_error_inside_a_200_body_is_an_error(transport):
    """A provider failing after OpenRouter accepted the request answers 200."""
    sent, queue = transport
    queue.append(FakeResponse(200, {"error": {"code": 502, "message": "provider died"}}))
    queue.append(_answer('{"x": 4}'))
    data, _ = chat_json("s", "u", SCHEMA, models=["a"], label="t")
    assert data == {"x": 4}
    assert len(sent) == 2


def test_unparseable_answer_is_retried(transport):
    sent, queue = transport
    queue.append(_answer("I think the answer is probably x"))
    queue.append(_answer('{"x": 5}'))
    data, _ = chat_json("s", "u", SCHEMA, models=["a"], label="t")
    assert data == {"x": 5}
    assert len(sent) == 2


def test_empty_answer_names_the_finish_reason(transport):
    _, queue = transport
    queue.append(FakeResponse(body={"choices": [{"message": {"content": None}, "finish_reason": "length"}]}))
    with pytest.raises(OpenRouterError, match="finish_reason=length"):
        chat_json("s", "u", SCHEMA, models=["a"], label="t", max_retries=1)


def test_data_policy_404_explains_the_privacy_setting(transport):
    _, queue = transport
    queue.append(FakeResponse(404, {"error": {
        "code": 404, "message": "No endpoints found matching your data policy"}}))
    with pytest.raises(OpenRouterError, match="settings/privacy"):
        chat_json("s", "u", SCHEMA, models=["a"], label="t")


def test_no_credits_explains_the_balance(transport):
    _, queue = transport
    queue.append(FakeResponse(402, {"error": {"code": 402, "message": "Insufficient credits"}}))
    with pytest.raises(OpenRouterError, match="positive balance"):
        chat_json("s", "u", SCHEMA, models=["a"], label="t")


@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('Here you go: {"a": {"b": 2}} hope it helps', {"a": {"b": 2}}),
])
def test_parse_json_payload_tolerates_wrapping(text, expected):
    assert parse_json_payload(text) == expected


def test_parse_json_payload_rejects_prose():
    with pytest.raises(ValueError):
        parse_json_payload("no json here")


def test_catalog_check_flags_missing_models():
    catalog = [
        {"id": "nvidia/nemotron-3-super-120b-a12b:free", "supported_parameters": ["structured_outputs"]},
        {"id": "meta/muse-spark-1.3-contributor", "supported_parameters": []},
    ]
    missing = openrouter.check_models(
        catalog, ["nvidia/nemotron-3-super-120b-a12b:free", "gone/model:free"]
    )
    assert missing == ["gone/model:free"]
    assert openrouter.is_free(catalog[0]) and openrouter.supports_json_schema(catalog[0])
    assert not openrouter.is_free(catalog[1]) and not openrouter.supports_json_schema(catalog[1])


def test_catalog_check_never_fails_the_pipeline(monkeypatch):
    def boom():
        raise ConnectionError("offline")

    monkeypatch.setattr(openrouter, "fetch_catalog", boom)
    assert openrouter.main() == 0
