"""Brave Search client.

Grounds world-price and review lookups in real search results. Gemini's own
`google_search` tool draws on the same per-model quota as spec extraction and
returns an answer with no visible sources; Brave has a separate budget and
hands back snippets that a model can be asked to read rather than recall.
"""
import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from app_config import BRAVE_API_KEY, BRAVE_TIMEOUT_SEC
from retry_utils import RateLimiter, call_with_retry


log = logging.getLogger(__name__)

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

_TAG_RE = re.compile(r"<[^>]+>")

# Free tier allows one request per second; the extra 0.1s absorbs clock skew.
_limiter = RateLimiter(1, window_sec=1.1)


def is_configured() -> bool:
    return bool(BRAVE_API_KEY)


def _fetch(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "X-Subscription-Token": BRAVE_API_KEY,
            "Accept": "application/json",
            "User-Agent": "notebookbuy/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=BRAVE_TIMEOUT_SEC) as response:
        return json.loads(response.read().decode())


def search(query: str, count: int = 5, freshness: str | None = None) -> list[dict]:
    """Return up to *count* web results as {title, url, description}.

    Never raises: a search failure has to degrade into the component estimate,
    not take down the nightly digest.
    """
    if not is_configured():
        return []

    params = {"q": query, "count": max(1, min(int(count), 10))}
    if freshness:
        params["freshness"] = freshness
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"

    try:
        data = call_with_retry(
            lambda: _fetch(url),
            max_retries=2,
            base_delay_sec=2.0,
            label="brave_search",
            limiter=_limiter,
        )
    except Exception as exc:
        log.warning("Brave search failed for %r: %s", query[:60], exc)
        return []

    results = []
    for item in (data.get("web") or {}).get("results", []):
        results.append({
            "title": _plain(item.get("title")),
            "url": item.get("url") or "",
            "description": _plain(item.get("description"))[:300],
        })
    return results


def _plain(value: str | None) -> str:
    """Brave marks query terms with <strong> and escapes entities; the model
    should read prices, not markup."""
    return html.unescape(_TAG_RE.sub("", value or "")).strip()


def as_context(results: list[dict], limit: int = 5) -> str:
    """Flatten results into the snippet block handed to the model."""
    lines = []
    for r in results[:limit]:
        lines.append(f"- {r['title']}\n  {r['url']}\n  {r['description']}")
    return "\n".join(lines)
