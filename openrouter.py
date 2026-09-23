"""OpenRouter client for structured (JSON) answers.

Plain HTTPS through ``requests`` rather than an SDK: the pipeline needs one
endpoint, and an OpenAI-compatible request body is easier to fake in tests than
a client object.

Model fallback happens server-side: the chain goes out as ``model`` plus
``models``, and OpenRouter moves on to the next model within the same request
when one errors, is rate-limited or is gone. The response names the model that
actually answered.

Run ``python openrouter.py`` to list the free models that currently support
structured outputs and to check that every configured model still exists — the
free roster changes month to month.
"""
import json
import logging
import os
import re
import sys
from typing import Any

import requests

from app_config import (
    AI_MAX_BACKOFF_SEC,
    AI_MAX_RETRIES,
    AI_RETRY_DELAY_SEC,
    AI_RPM_LIMIT,
    AI_TIMEOUT_SEC,
    OPENROUTER_API_KEY,
    OPENROUTER_FALLBACK_MODELS,
    OPENROUTER_MODEL,
    OPENROUTER_REVIEW_MODEL,
)
from retry_utils import RateLimiter, call_with_retry


log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"

# One budget for the whole process: OpenRouter's 20 RPM cap on :free models is
# per account, not per model, so every caller has to draw from the same bucket.
_limiter = RateLimiter(AI_RPM_LIMIT, window_sec=60.0)

# A retry cannot fix these: malformed request, bad key, empty balance,
# moderation refusal, no endpoint for the model or the account's data policy.
_PERMANENT_STATUSES = frozenset({400, 401, 402, 403, 404})

_FENCE_RE = re.compile(r"^```[a-z]*\s*|\s*```$", re.IGNORECASE)


class OpenRouterError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(f"{status} {message}" if status else message)
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status not in _PERMANENT_STATUSES


def is_configured() -> bool:
    return bool(OPENROUTER_API_KEY)


def _post(payload: dict) -> requests.Response:
    return requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            # Attribution headers: they label the traffic on the OpenRouter
            # dashboard and change nothing about routing.
            "HTTP-Referer": "https://github.com/zabrodschiipavel-sketch/notebookbuy",
            "X-OpenRouter-Title": "NotebookBuy",
        },
        json=payload,
        timeout=AI_TIMEOUT_SEC,
    )


def _hint(status: int | None, message: str) -> str:
    """Turn the errors that need a settings change into ones that say which."""
    lowered = message.lower()
    if status == 401:
        return f"{message} — check OPENROUTER_API_KEY"
    if status == 402:
        return (f"{message} — paid fallback models (Muse Spark Contributor) need a "
                "positive balance: https://openrouter.ai/settings/credits")
    if status == 404 and ("data policy" in lowered or "guardrail" in lowered):
        return (f"{message} — free and Contributor-tier models may train on prompts; "
                "allow that at https://openrouter.ai/settings/privacy")
    return message


def _retry_after(response: requests.Response) -> float | None:
    try:
        return float(response.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None


def _complete(payload: dict) -> dict:
    response = _post(payload)
    try:
        body = response.json()
    except ValueError:
        body = None
    # Errors arrive as a non-200 status, or as a 200 whose body is an error
    # (a provider failing after OpenRouter already accepted the request).
    error = body.get("error") if isinstance(body, dict) else None
    if response.status_code != 200 or error or not isinstance(body, dict):
        error = error if isinstance(error, dict) else {}
        code = error.get("code")
        status = code if isinstance(code, int) else response.status_code
        message = str(error.get("message") or response.text[:300] or "no response body")
        raise OpenRouterError(_hint(status, message), status=status, retry_after=_retry_after(response))
    return body


def _answer_text(body: dict) -> str:
    choices = body.get("choices") or []
    if not choices:
        raise OpenRouterError("response has no choices")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, list):  # content parts instead of a plain string
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content or not str(content).strip():
        # Usually a reasoning model that spent max_tokens thinking.
        raise OpenRouterError(f"empty answer (finish_reason={choices[0].get('finish_reason')})")
    return str(content)


def parse_json_payload(text: str) -> Any:
    """JSON out of a model answer, tolerating code fences and prose around it.

    Structured outputs are requested on every call, but a fallback model that
    does not support them answers in free form — usually fenced JSON.
    """
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        return json.loads(cleaned[start:end + 1])
    raise ValueError("no JSON object in the answer")


def same_model(a: str, b: str) -> bool:
    """Compare model ids ignoring the variant suffix (``:free``)."""
    return a.split(":", 1)[0] == b.split(":", 1)[0]


def chat_json(
    system: str,
    user: str,
    schema: dict,
    *,
    models: list[str],
    label: str,
    temperature: float = 0.0,
    max_tokens: int = 4000,
    reasoning_effort: str | None = None,
    max_retries: int = AI_MAX_RETRIES,
) -> tuple[Any, str]:
    """Ask *models* (first = primary, rest = fallbacks) for a JSON answer.

    Returns ``(parsed_json, model_that_answered)``. Raises the last error once
    retries are spent — callers decide how to degrade.
    """
    if not models:
        raise ValueError("chat_json needs at least one model")

    # The schema is spelled out in the prompt as well: a fallback without
    # structured-output support would otherwise have to guess the shape.
    schema_note = (
        "Reply with one JSON object that matches this JSON Schema and nothing else:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    payload: dict[str, Any] = {
        "model": models[0],
        "messages": [
            {"role": "system", "content": f"{system}\n\n{schema_note}"},
            {"role": "user", "content": user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "answer", "strict": True, "schema": schema},
        },
        # Repairs near-miss JSON (a trailing comma, a missing brace) server-side.
        "plugins": [{"id": "response-healing"}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if len(models) > 1:
        payload["models"] = list(models[1:])
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}

    def attempt() -> tuple[Any, str]:
        body = _complete(payload)
        served = str(body.get("model") or models[0])
        text = _answer_text(body)
        try:
            return parse_json_payload(text), served
        except ValueError as exc:
            # Sampling can fix a malformed answer, so this one is retryable.
            raise OpenRouterError(f"unparseable answer from {served}: {exc}") from exc

    data, served = call_with_retry(
        attempt,
        max_retries=max_retries,
        base_delay_sec=AI_RETRY_DELAY_SEC,
        label=label,
        max_delay_sec=AI_MAX_BACKOFF_SEC,
        limiter=_limiter,
    )
    if not same_model(served, models[0]):
        log.warning("%s: %s unavailable, answered by fallback %s", label, models[0], served)
    return data, served


# ================= Model catalog check =================

def fetch_catalog() -> list[dict]:
    """Public model list; needs no key and does not count against any quota."""
    response = requests.get(MODELS_URL, timeout=30)
    response.raise_for_status()
    data = response.json().get("data", [])
    return data if isinstance(data, list) else []


def is_free(model: dict) -> bool:
    return str(model.get("id", "")).endswith(":free")


def supports_json_schema(model: dict) -> bool:
    return "structured_outputs" in (model.get("supported_parameters") or [])


def configured_models() -> list[str]:
    chain = [OPENROUTER_MODEL, *OPENROUTER_FALLBACK_MODELS, OPENROUTER_REVIEW_MODEL]
    return list(dict.fromkeys(chain))


def check_models(catalog: list[dict], models: list[str]) -> list[str]:
    """Configured model ids that are missing from the catalog."""
    known = {str(m.get("id", "")) for m in catalog}
    return [m for m in models if m not in known]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        catalog = fetch_catalog()
    except Exception as exc:
        log.error("Could not fetch the OpenRouter catalog: %s", exc)
        return 0  # an advisory check must not fail the pipeline

    by_id = {str(m.get("id", "")): m for m in catalog}
    missing = check_models(catalog, configured_models())
    for model_id in configured_models():
        model = by_id.get(model_id)
        if model is None:
            status = "MISSING from the catalog"
        else:
            status = ("free" if is_free(model) else "paid") + (
                ", structured outputs" if supports_json_schema(model) else ", NO structured outputs"
            )
        log.info("configured  %-50s %s", model_id, status)
    if missing and os.getenv("GITHUB_ACTIONS") == "true":
        print(
            f"::warning title=OpenRouter model gone::{', '.join(missing)} not in the "
            "catalog — AI calls fall through to the next model. Run `python openrouter.py` "
            "to pick a replacement.",
            flush=True,
        )

    candidates = sorted(
        (m for m in catalog if is_free(m) and supports_json_schema(m)),
        key=lambda m: m.get("context_length") or 0,
        reverse=True,
    )
    log.info("\nFree models with structured outputs (%d):", len(candidates))
    for m in candidates:
        log.info("  %-55s ctx=%s", m.get("id"), m.get("context_length"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
