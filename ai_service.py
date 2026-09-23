"""AI layer: spec extraction, digest review and reading search snippets.

Every call goes through OpenRouter (see openrouter.py) and asks for JSON.
"""
import json
import logging
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import openrouter
import web_search
from app_config import (
    AI_EXTRACT_BATCH_SIZE,
    AI_MAX_WORKERS,
    BRAVE_RESULTS,
    OPENROUTER_FALLBACK_MODELS,
    OPENROUTER_MODEL,
    OPENROUTER_REVIEW_MODEL,
)


log = logging.getLogger(__name__)

ChatFn = Callable[..., tuple[Any, str]]

_VERDICTS = ("exclude", "suspicious", "ok", "great")
_LEADING_INT_RE = re.compile(r"\s*(\d+)")


def _annotate_ci(message: str) -> None:
    """Raise a GitHub Actions annotation so silent degradation is visible.

    The daily workflow exits 0 even when most AI calls fail, so a warning buried
    in a thousand log lines is not enough — an annotation surfaces on the run.
    """
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::warning title=AI extraction degraded::{message}", flush=True)


def model_chain(primary: str) -> list[str]:
    """Primary first, then the configured fallbacks, without duplicates."""
    return list(dict.fromkeys([primary, *OPENROUTER_FALLBACK_MODELS]))


def _object(properties: dict) -> dict:
    """Strict-mode JSON Schema object: every property required, nothing else."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _as_int(value: Any) -> int:
    """16, "16" and "16GB" all mean 16 — only the fallback model is loose about types."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    match = _LEADING_INT_RE.match(str(value or ""))
    return int(match.group(1)) if match else 0


def _as_storage_gb(value: Any) -> int:
    """Like _as_int, but "1TB" is 1024 rather than 1."""
    size = _as_int(value)
    return size * 1024 if isinstance(value, str) and "tb" in value.lower() else size


def _as_float(value: Any) -> float:
    try:
        return max(0.0, float(str(value).replace(",", "").lstrip("$").strip()))
    except (TypeError, ValueError):
        return 0.0


def _clean_specs(item: Any) -> dict | None:
    if not isinstance(item, dict) or not str(item.get("id", "")).strip():
        return None
    broken = item.get("is_broken")
    return {
        "id": str(item["id"]).strip(),
        "cpu": str(item.get("cpu") or "").strip(),
        "gpu": str(item.get("gpu") or "").strip() or "integrated",
        "ram": _as_int(item.get("ram")),
        "ssd": _as_storage_gb(item.get("ssd")),
        "is_broken": broken is True or str(broken).strip().lower() == "true",
    }


class AIService:
    """Structured spec extraction, digest review and snippet reading."""

    _SPECS_SCHEMA = _object({
        "laptops": {
            "type": "array",
            "items": _object({
                "id": {"type": "string"},
                "cpu": {"type": "string"},
                "gpu": {"type": "string"},
                "ram": {"type": "integer"},
                "ssd": {"type": "integer"},
                "is_broken": {"type": "boolean"},
            }),
        },
    })

    _EXTRACT_SYSTEM_PROMPT = (
        "Extract laptop specs from used-laptop listings. For every listing return "
        "one entry with its id copied exactly and: cpu — full model (e.g. 'Intel "
        "Core i7-12700H'), empty string if the listing does not say; gpu — "
        "discrete GPU model or 'integrated'; ram and ssd — GB as integers, 0 if "
        "not stated; is_broken — true if sold for parts, broken or locked."
    )

    _REVIEW_SCHEMA = _object({
        "reviews": {
            "type": "array",
            "items": _object({
                "id": {"type": "string"},
                "verdict": {"type": "string", "enum": list(_VERDICTS)},
                "reason": {"type": "string"},
            }),
        },
    })

    _WORLD_PRICE_SCHEMA = _object({
        "launch_usd": {"type": "number"},
        "current_usd": {"type": "number"},
    })

    _NBC_SCHEMA = _object({
        "score": {"type": "integer"},
        "url": {"type": "string"},
    })

    def __init__(self, chat: ChatFn | None = None):
        # Injectable so tests never touch the network.
        self._chat: ChatFn = chat or openrouter.chat_json
        self.enabled = chat is not None or openrouter.is_configured()

    def extract_specs(self, ads_list: list[dict]) -> list[dict]:
        """Specs for ads the regex could not parse, AI_EXTRACT_BATCH_SIZE per request.

        Batching is what fits a morning run into the free tier: the binding
        limit is requests per day, and ten ads cost the same one request as one.
        """
        if not self.enabled:
            log.warning("Skipping AI extraction because OPENROUTER_API_KEY is not configured")
            return []

        batches = [
            ads_list[i:i + AI_EXTRACT_BATCH_SIZE]
            for i in range(0, len(ads_list), AI_EXTRACT_BATCH_SIZE)
        ]
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=AI_MAX_WORKERS) as executor:
            futures = [executor.submit(self._extract_batch, batch) for batch in batches]
            for future in as_completed(futures):
                results.extend(future.result())

        failed = len(ads_list) - len(results)
        # A run that silently drops most ads still exits 0, so the failure count
        # has to be loud enough to notice in the daily workflow log.
        log.log(
            logging.WARNING if failed else logging.INFO,
            "AI extraction: %s/%s ads parsed, %s failed",
            len(results), len(ads_list), failed,
        )
        if ads_list and failed / len(ads_list) > 0.2:
            _annotate_ci(
                f"AI extraction dropped {failed} of {len(ads_list)} ads "
                f"(likely OpenRouter rate limits) — those ads are missing from today's ranking."
            )
        return results

    def _extract_batch(self, batch: list[dict]) -> list[dict]:
        wanted = {str(ad["id"]) for ad in batch}
        listings = [
            {"id": str(ad["id"]), "title": ad["title"], "description": ad["text"][:2000]}
            for ad in batch
        ]
        try:
            data, _ = self._chat(
                self._EXTRACT_SYSTEM_PROMPT,
                "Listings:\n" + json.dumps(listings, ensure_ascii=False),
                self._SPECS_SCHEMA,
                models=model_chain(OPENROUTER_MODEL),
                label=f"extract_specs:{len(batch)} ads",
                max_tokens=8000,
                reasoning_effort="low",
            )
        except Exception as e:
            log.warning("AI extraction failed for a batch of %d: %s", len(batch), e)
            return []

        items = data.get("laptops", []) if isinstance(data, dict) else []
        results: dict[str, dict] = {}
        for item in items:
            specs = _clean_specs(item)
            # An id the batch never contained is a hallucination; a repeat
            # keeps its first answer.
            if specs and specs["id"] in wanted and specs["id"] not in results:
                results[specs["id"]] = specs
        return list(results.values())

    # Criterion (2) is carried by the caveat as much as by the rule: an
    # M-series Mac with a 128GB SSD is a parsing error, but a 2015 Intel Air
    # with one is genuine, and a model told only "128GB Airs are fake" trades
    # a miss for a false rejection. The "when unsure, treat as plausible" line
    # is there for the same reason — this verdict deletes listings.
    _REVIEW_SYSTEM_PROMPT = (
        "You review used-laptop listings from the Moldovan marketplace 999.md "
        "before they reach a buyer's Telegram digest. For each listing judge: "
        "(1) do the parsed specs contradict the model in the title (e.g. a "
        "Xiaomi or 2013-era laptop 'with' an Apple M2 or i7-14700HX, 128GB RAM "
        "on a budget machine — usually parsing errors); "
        "(2) is this a configuration the manufacturer ever actually sold? Apple "
        "silicon (M1/M2/M3/M4) starts at 256GB storage and ships only 8/16/24/32/"
        "36/48GB of unified memory — an M-series Mac listed with 64GB or 128GB "
        "storage is a parsing error, while a pre-2018 Intel MacBook Air with a "
        "128GB SSD is genuine and must NOT be rejected. Judge non-Apple machines "
        "the same way: a current high-end CPU paired with 4GB of RAM, or storage "
        "smaller than the RAM, is parsed wrong rather than a real bargain. "
        "When a model's real options are not something you know, treat the "
        "configuration as plausible instead of guessing; "
        "(3) does the deal look legitimate (a near-new MacBook at a fraction of "
        "market price is a scam or an iCloud/MDM-locked unit); "
        "(4) is it a real sale ad at all (not an accessories ad, a description "
        "fragment, or shop spam). "
        "Verdicts: 'exclude' = certain garbage/scam, must not be shown; "
        "'suspicious' = show but warn the buyer what to verify; 'ok' = "
        "plausible; 'great' = specs consistent and genuinely good value. "
        "reason: at most 12 words, in Russian."
    )

    def review_deals(self, deals: list[dict], model: str | None = None) -> dict[str, dict]:
        """Sanity-check the top digest deals before they are sent.

        One request for the whole batch; OpenRouter walks the fallback chain
        itself if the primary is down or out of quota. Returns
        {ad_id: {"verdict", "reason"}}; an empty dict when AI is unavailable or
        every model fails, so callers degrade into sending the digest unreviewed.
        An explicit *model* is used alone, without fallbacks.
        """
        if not self.enabled or not deals:
            return {}

        payload = [
            {
                "id": str(d["id"]),
                "title": d.get("title", ""),
                "price_mdl": d.get("price", 0),
                "parsed_cpu": d.get("cpu", ""),
                "parsed_ram_gb": d.get("ram", 0),
                "parsed_ssd_gb": d.get("ssd", 0),
                "vs_world": d.get("vs_str", ""),
                "heuristic_risk": d.get("risk", ""),
                "description": str(d.get("description", ""))[:600],
            }
            for d in deals
        ]
        try:
            data, served = self._chat(
                self._REVIEW_SYSTEM_PROMPT,
                "Review these listings:\n" + json.dumps(payload, ensure_ascii=False, indent=1),
                self._REVIEW_SCHEMA,
                models=[model] if model else model_chain(OPENROUTER_REVIEW_MODEL),
                label="review_deals",
                temperature=0.1,
                max_tokens=8000,
            )
        except Exception as e:
            log.warning("AI deal review failed, sending digest without it: %s", e)
            return {}

        log.info("Deal review done with %s", served)
        reviews = data.get("reviews", []) if isinstance(data, dict) else []
        out = {}
        for r in reviews:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            verdict = str(r.get("verdict", "")).strip().lower()
            # A verdict outside the enum (a fallback without schema support
            # saying "reject") is not guessed at: the listing stays unreviewed.
            if verdict in _VERDICTS:
                out[str(r["id"])] = {"verdict": verdict, "reason": str(r.get("reason", ""))}
        return out

    def _extract_json(self, instruction: str, context: str, schema: dict, label: str) -> dict[str, Any]:
        """Read structured data out of search snippets."""
        if not self.enabled:
            return {}
        try:
            data, _ = self._chat(
                instruction,
                f"Результаты поиска:\n{context}",
                schema,
                models=model_chain(OPENROUTER_MODEL),
                label=label,
                max_tokens=2000,
                reasoning_effort="low",
            )
        except Exception as e:
            log.warning("%s failed: %s", label, e)
            return {}
        return data if isinstance(data, dict) else {}

    def lookup_world_price(self, title: str, cpu: str, gpu: str) -> dict[str, Any]:
        """Launch and current USD price, read out of real search results."""
        results = web_search.search(f"{title} {cpu} laptop price USD", count=BRAVE_RESULTS)
        if not results:
            return {}
        data = self._extract_json(
            "Из результатов поиска определи цены НОВОГО ноутбука в долларах США: "
            "launch_usd — цена на старте продаж, current_usd — актуальная розничная. "
            "Бери только цены самого ноутбука, не аксессуаров и не б/у. "
            "Если данных нет — верни 0 в соответствующем поле.",
            web_search.as_context(results),
            self._WORLD_PRICE_SCHEMA,
            f"world_price:{title[:30]}",
        )
        # The digest multiplies these by an exchange rate; "899" from a model
        # that ignored the schema must not reach it as a string.
        prices = {key: _as_float(data.get(key)) for key in ("launch_usd", "current_usd")}
        # All zeros is the model saying "not found". Returned as data it was
        # cached as a hit forever; empty, it becomes a miss that expires.
        return prices if any(prices.values()) else {}

    def lookup_nbc_score(self, title: str, cpu: str, gpu: str) -> dict[str, Any]:
        """Notebookcheck verdict for the model, read out of real search results."""
        results = web_search.search(
            f"notebookcheck review {title} {cpu} rating", count=BRAVE_RESULTS
        )
        if not results:
            return {}
        data = self._extract_json(
            "Найди в результатах итоговую оценку Notebookcheck для этого ноутбука "
            "в процентах и ссылку на обзор. Оценка Notebookcheck обычно 50–95%. "
            "Если оценки нет — верни score 0 и пустой url. Не выдумывай число.",
            web_search.as_context(results),
            self._NBC_SCHEMA,
            f"nbc_score:{title[:30]}",
        )
        # Score 0 means "no rating found" — a miss, not a rating (see above).
        return data if _as_int(data.get("score")) else {}

