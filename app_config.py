"""
Runtime configuration from environment (.env).
Keeps scoring.py free of I/O and side effects.
"""
import os

from dotenv import load_dotenv

from scoring import MIN_CPU_SCORE as DEFAULT_MIN_CPU_SCORE


load_dotenv()


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_list(key: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(key, default).split(",") if item.strip()]


# ---- AI: every model call goes through OpenRouter ----
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
# Every AI job here — spec extraction, reading prices out of search snippets,
# reviewing the digest — asks for JSON, so the primary has to be a free model
# that honours structured outputs, not just the one ranked highest. Nemotron 3
# Ultra tops the free usage charts but ignores response_format; Super enforces
# the schema. The free roster churns monthly: `python openrouter.py` lists what
# is free right now and flags a configured model that has disappeared.
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"
).strip()
# Tried in order, server-side, within the same request whenever the model before
# it errors, is rate-limited or is gone. Muse Spark Contributor is not a :free
# variant — it bills a few cents a month at this volume and needs a positive
# balance — but it has its own rate limits, so it keeps working once the shared
# free-model daily quota is spent.
OPENROUTER_FALLBACK_MODELS = _env_list(
    "OPENROUTER_FALLBACK_MODELS", "meta/muse-spark-1.3-contributor"
)
# The digest review runs once a day and its verdict deletes listings, so it is
# the one call worth pointing at a stronger model. Defaults to the primary.
OPENROUTER_REVIEW_MODEL = os.getenv("OPENROUTER_REVIEW_MODEL", "").strip() or OPENROUTER_MODEL

# AI review of the Telegram digest top: catches scam listings and parsing
# garbage (e.g. "Xiaomi with Apple M2", 128GB RAM from a 128GB SSD).
ENABLE_AI_REVIEW = _env_bool("ENABLE_AI_REVIEW", True)
AI_REVIEW_TOP_N = max(0, _env_int("AI_REVIEW_TOP_N", 15))

# Repeat handling. A listing already shown, at a price that has not moved, is
# not news — it is held back for this many days. Any price change makes it
# eligible again immediately.
DIGEST_REPEAT_COOLDOWN_DAYS = max(0, _env_int("DIGEST_REPEAT_COOLDOWN_DAYS", 7))
# ...but never at the cost of an empty digest: held-back listings are added
# back, best first, until the digest has at least this many entries.
DIGEST_MIN_DEALS = max(0, _env_int("DIGEST_MIN_DEALS", 3))

# Second, city-specific section of the digest. It is rendered only when it has
# something in it: the hardcoded Bălți block sat empty in 91% of past digests
# and cost a heading plus a "nothing found" line every single day.
DIGEST_REGION = os.getenv("DIGEST_REGION", "Бельцы").strip()

# "Got cheaper" block. The scraper already detects every price drop (141 on a
# typical morning) and only logged them. Bounds matter: a 99% "drop" is a
# seller fixing a 111111 MDL typo, not a bargain.
DIGEST_PRICE_DROPS_N = max(0, _env_int("DIGEST_PRICE_DROPS_N", 3))
PRICE_DROP_MIN_PCT = max(1.0, _env_float("PRICE_DROP_MIN_PCT", 10.0))
PRICE_DROP_MAX_PCT = max(1.0, _env_float("PRICE_DROP_MAX_PCT", 70.0))

# OpenRouter caps :free models at 20 requests per minute and — the limit that
# actually binds — 50 per day, or 1000 per day once $10 of credits has ever been
# bought. Extraction therefore sends ads in batches: ~60 unparsed ads a morning
# become ~6 requests instead of 60.
AI_EXTRACT_BATCH_SIZE = max(1, _env_int("AI_EXTRACT_BATCH_SIZE", 10))
AI_MAX_WORKERS = max(1, _env_int("AI_MAX_WORKERS", 2))
AI_RPM_LIMIT = max(1, _env_int("AI_RPM_LIMIT", 16))  # free-model cap is 20
AI_MAX_RETRIES = max(1, _env_int("AI_MAX_RETRIES", 3))
AI_RETRY_DELAY_SEC = max(0.1, _env_float("AI_RETRY_DELAY_SEC", 2.0))
AI_SEARCH_DELAY_SEC = max(0.0, _env_float("AI_SEARCH_DELAY_SEC", 1.5))
# Longest single back-off. The digest runs once a day, so waiting out the
# server's suggested delay is cheaper than dropping the ad.
AI_MAX_BACKOFF_SEC = max(1.0, _env_float("AI_MAX_BACKOFF_SEC", 65.0))
# Free endpoints queue, and reasoning models think before answering.
AI_TIMEOUT_SEC = max(10.0, _env_float("AI_TIMEOUT_SEC", 120.0))

ENABLE_EXTERNAL_LOOKUPS = _env_bool("ENABLE_EXTERNAL_LOOKUPS", True)

# Brave Search grounds the world-price and Notebookcheck lookups in real
# results instead of asking a model to recall a price. Free tier: 1 request per
# second, 2000 per month — and that budget may be shared with other projects
# using the same key, so lookups stay capped and cached.
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
BRAVE_TIMEOUT_SEC = max(5.0, _env_float("BRAVE_TIMEOUT_SEC", 20.0))
BRAVE_RESULTS = max(1, _env_int("BRAVE_RESULTS", 5))
# How long a failed world-price / Notebookcheck lookup stays remembered. Without
# negative caching every run re-asks for the same laptops that have no answer,
# which is exactly the quota the ads themselves need.
EXTERNAL_MISS_TTL_DAYS = max(1, _env_int("EXTERNAL_MISS_TTL_DAYS", 7))

MIN_CPU_SCORE = _env_int("MIN_CPU_SCORE", DEFAULT_MIN_CPU_SCORE)

# The scrape pages through the whole category. It used to be one 500-ad request,
# and every listing past it dropped out of the ranking and price tracking.
SCRAPE_PAGE_SIZE = max(10, _env_int("SCRAPE_PAGE_SIZE", 200))
SCRAPE_MAX_ADS = max(1, _env_int("SCRAPE_MAX_ADS", 3000))
SCRAPE_PAGE_DELAY_SEC = max(0.0, _env_float("SCRAPE_PAGE_DELAY_SEC", 1.5))
# Regex parsing is cheap, so the analyzer looks at everything scraped.
ADS_ANALYZE_LIMIT = max(1, _env_int("ADS_ANALYZE_LIMIT", SCRAPE_MAX_ADS))
# AI extraction is not: it draws on the free model's daily request quota,
# which the price lookups and the digest review share. Ads over this cap wait
# for the next run (newest first), so a big backlog clears over a few days
# instead of starving the review.
AI_EXTRACT_MAX_ADS = max(0, _env_int("AI_EXTRACT_MAX_ADS", 200))

PASSMARK_CACHE_DAYS = max(1, _env_int("PASSMARK_CACHE_DAYS", 7))
WORLD_PRICE_TOP_N = max(0, _env_int("WORLD_PRICE_TOP_N", 10))

DB_NAME = os.getenv("DB_NAME", "laptops_database.db")
