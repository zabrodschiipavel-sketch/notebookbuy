"""Gemini AI service for laptop spec extraction and grounded web search."""
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from google import genai
from google.genai import types

import web_search
from app_config import (
    BRAVE_RESULTS,
    GEMINI_API_KEY,
    GEMINI_MAX_BACKOFF_SEC,
    GEMINI_MAX_RETRIES,
    GEMINI_MAX_WORKERS,
    GEMINI_MODEL,
    GEMINI_REQUEST_DELAY_SEC,
    GEMINI_REVIEW_FALLBACK_MODELS,
    GEMINI_REVIEW_MODEL,
    GEMINI_RPM_LIMIT,
    GEMINI_RPM_LIMIT_FULL,
    GEMINI_SEARCH_DELAY_SEC,
)
from retry_utils import RateLimiter, call_with_retry


log = logging.getLogger(__name__)


def _annotate_ci(message: str) -> None:
    """Raise a GitHub Actions annotation so silent degradation is visible.

    The daily workflow exits 0 even when most AI calls fail, so a warning buried
    in a thousand log lines is not enough — an annotation surfaces on the run.
    """
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::warning title=AI extraction degraded::{message}", flush=True)


class AIService:
    """Wraps Google Gemini for structured spec extraction and Google Search tool."""

    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        # Gemini's RPM quota is per model, so each model gets its own budget;
        # the extraction pass must not starve the digest review that follows it.
        self._limiters: dict[str, RateLimiter] = {}
        self._limiters_lock = threading.Lock()
        self.schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "cpu": types.Schema(type=types.Type.STRING),
                "gpu": types.Schema(type=types.Type.STRING),
                "ram": types.Schema(type=types.Type.INTEGER),
                "ssd": types.Schema(type=types.Type.INTEGER),
                "is_broken": types.Schema(type=types.Type.BOOLEAN),
            },
            required=["cpu", "gpu", "ram", "ssd", "is_broken"]
        )
        self.system_prompt = (
            "Extract laptop specs accurately. cpu: full model (e.g. 'Intel Core i7-12700H'). "
            "gpu: model or 'integrated'. ram/ssd: GB as integers. is_broken: true if parts/broken."
        )

    @staticmethod
    def rpm_for(model: str) -> int:
        """Free-tier RPM differs by tier: flash-lite 15, full flash 5."""
        return GEMINI_RPM_LIMIT if "lite" in model.lower() else GEMINI_RPM_LIMIT_FULL

    def limiter_for(self, model: str) -> RateLimiter:
        """Return the shared per-model RPM limiter, creating it on first use."""
        with self._limiters_lock:
            limiter = self._limiters.get(model)
            if limiter is None:
                limiter = RateLimiter(self.rpm_for(model), window_sec=60.0)
                self._limiters[model] = limiter
            return limiter

    def extract_specs(self, ads_list: list[dict]) -> list[dict]:
        if not self.client:
            log.warning("Skipping AI extraction because GEMINI_API_KEY is not configured")
            return []

        def process_one(ad: dict) -> dict | None:
            try:
                content = f"Title: {ad['title']}\nDescription: {ad['text'][:2000]}"

                def _call():
                    return self.client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=[content],
                        config=types.GenerateContentConfig(
                            system_instruction=self.system_prompt,
                            response_mime_type="application/json",
                            response_schema=self.schema,
                            temperature=0.0,
                        ),
                    )

                response = call_with_retry(
                    _call,
                    max_retries=GEMINI_MAX_RETRIES,
                    base_delay_sec=GEMINI_REQUEST_DELAY_SEC or 1.0,
                    label=f"extract_specs:{ad['id']}",
                    max_delay_sec=GEMINI_MAX_BACKOFF_SEC,
                    limiter=self.limiter_for(GEMINI_MODEL),
                )
                data = json.loads(response.text)
                data["id"] = ad["id"]
                return data
            except Exception as e:
                log.warning(f"AI failed for {ad['id']}: {e}")
                return None

        results = []
        with ThreadPoolExecutor(max_workers=GEMINI_MAX_WORKERS) as executor:
            futures = [executor.submit(process_one, ad) for ad in ads_list]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    results.append(res)

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
                f"(likely Gemini rate limiting) — those ads are missing from today's ranking."
            )
        return results

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

        Uses full flash rather than the flash-lite model that does extraction:
        on real listings from past digests it caught 3 of 4 planted scams
        against flash-lite's 2, and this runs once a day on ~15 listings.
        When the primary fails the review walks down
        GEMINI_REVIEW_FALLBACK_MODELS. Returns {ad_id: {"verdict", "reason"}};
        an empty dict when the client is unavailable or every model fails, so
        callers degrade into sending the digest unreviewed.
        """
        if not self.client or not deals:
            return {}

        models_to_try = [model or GEMINI_REVIEW_MODEL]
        if not model:
            models_to_try += [m for m in GEMINI_REVIEW_FALLBACK_MODELS if m not in models_to_try]

        review_schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "reviews": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "id": types.Schema(type=types.Type.STRING),
                            "verdict": types.Schema(
                                type=types.Type.STRING,
                                enum=["exclude", "suspicious", "ok", "great"],
                            ),
                            "reason": types.Schema(type=types.Type.STRING),
                        },
                        required=["id", "verdict", "reason"],
                    ),
                )
            },
            required=["reviews"],
        )

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

        for i, model_name in enumerate(models_to_try):
            # Models with a fallback behind them get a single attempt — a dead
            # model (free-tier pro = quota 0) or a 503 spike should not stall
            # the chain. Only the last model retries patiently: the digest
            # runs once a day, so waiting out a demand spike is worth it.
            is_last = i == len(models_to_try) - 1
            try:
                resp = call_with_retry(
                    lambda m=model_name: self.client.models.generate_content(
                        model=m,
                        contents="Review these listings:\n" + json.dumps(payload, ensure_ascii=False, indent=1),
                        config=types.GenerateContentConfig(
                            system_instruction=self._REVIEW_SYSTEM_PROMPT,
                            response_mime_type="application/json",
                            response_schema=review_schema,
                            temperature=0.1,
                        ),
                    ),
                    max_retries=GEMINI_MAX_RETRIES if is_last else 1,
                    base_delay_sec=max(5.0, GEMINI_REQUEST_DELAY_SEC),
                    label=f"review_deals:{model_name}",
                    max_delay_sec=GEMINI_MAX_BACKOFF_SEC,
                    limiter=self.limiter_for(model_name),
                )
                data = json.loads(resp.text)
                log.info("Deal review done with %s", model_name)
                return {
                    str(r["id"]): {"verdict": r["verdict"], "reason": r["reason"]}
                    for r in data.get("reviews", [])
                    if r.get("id")
                }
            except Exception as e:
                log.warning(f"AI deal review with {model_name} failed: {e}")
        log.warning("All review models failed; sending digest without AI review")
        return {}

    def _extract_json(self, instruction: str, context: str, schema, label: str) -> dict[str, Any]:
        """Read structured data out of search snippets (no grounding tool)."""
        if not self.client:
            return {}
        try:
            resp = call_with_retry(
                lambda: self.client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=f"{instruction}\n\nРезультаты поиска:\n{context}",
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=0.0,
                    ),
                ),
                max_retries=GEMINI_MAX_RETRIES,
                base_delay_sec=GEMINI_SEARCH_DELAY_SEC or 1.0,
                label=label,
                max_delay_sec=GEMINI_MAX_BACKOFF_SEC,
                limiter=self.limiter_for(GEMINI_MODEL),
            )
            data = json.loads(resp.text)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            log.warning("%s failed: %s", label, e)
            return {}

    _WORLD_PRICE_SCHEMA = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "launch_usd": types.Schema(type=types.Type.NUMBER),
            "current_usd": types.Schema(type=types.Type.NUMBER),
        },
        required=["launch_usd", "current_usd"],
    )

    _NBC_SCHEMA = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "score": types.Schema(type=types.Type.INTEGER),
            "url": types.Schema(type=types.Type.STRING),
        },
        required=["score", "url"],
    )

    def lookup_world_price(self, title: str, cpu: str, gpu: str) -> dict[str, Any]:
        """Launch and current USD price, read out of real search results."""
        results = web_search.search(f"{title} {cpu} laptop price USD", count=BRAVE_RESULTS)
        if not results:
            return {}
        return self._extract_json(
            "Из результатов поиска определи цены НОВОГО ноутбука в долларах США: "
            "launch_usd — цена на старте продаж, current_usd — актуальная розничная. "
            "Бери только цены самого ноутбука, не аксессуаров и не б/у. "
            "Если данных нет — верни 0 в соответствующем поле.",
            web_search.as_context(results),
            self._WORLD_PRICE_SCHEMA,
            f"world_price:{title[:30]}",
        )

    def lookup_nbc_score(self, title: str, cpu: str, gpu: str) -> dict[str, Any]:
        """Notebookcheck verdict for the model, read out of real search results."""
        results = web_search.search(
            f"notebookcheck review {title} {cpu} rating", count=BRAVE_RESULTS
        )
        if not results:
            return {}
        return self._extract_json(
            "Найди в результатах итоговую оценку Notebookcheck для этого ноутбука "
            "в процентах и ссылку на обзор. Оценка Notebookcheck обычно 50–95%. "
            "Если оценки нет — верни score 0 и пустой url. Не выдумывай число.",
            web_search.as_context(results),
            self._NBC_SCHEMA,
            f"nbc_score:{title[:30]}",
        )

