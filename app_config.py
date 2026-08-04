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


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
# Stronger model used to sanity-check the final top before it is sent.
# NOTE: pro models need a billing-enabled API key; on the free tier the review
# automatically falls back through GEMINI_REVIEW_FALLBACK_MODELS (comma-
# separated; multiple entries survive a temporary 503 on one model).
GEMINI_PRO_MODEL = os.getenv("GEMINI_PRO_MODEL", "gemini-pro-latest")
GEMINI_REVIEW_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_REVIEW_FALLBACK_MODEL", "gemini-flash-latest,gemini-2.5-flash"
    ).split(",")
    if m.strip()
]

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

GEMINI_MAX_WORKERS = max(1, _env_int("GEMINI_MAX_WORKERS", 3))
GEMINI_REQUEST_DELAY_SEC = max(0.0, _env_float("GEMINI_REQUEST_DELAY_SEC", 0.5))
GEMINI_SEARCH_DELAY_SEC = max(0.0, _env_float("GEMINI_SEARCH_DELAY_SEC", 1.5))
GEMINI_MAX_RETRIES = max(1, _env_int("GEMINI_MAX_RETRIES", 3))
# Requests per minute allowed *per model*. Gemini's free tier grants 15 RPM
# (quota GenerateRequestsPerMinutePerProjectPerModel); without client-side
# throttling the worker pool spends that budget in seconds and everything after
# it returns 429. Keep a little headroom for clock skew and retries.
GEMINI_RPM_LIMIT = max(1, _env_int("GEMINI_RPM_LIMIT", 12))
# Longest single back-off. The digest runs once a day, so waiting out the
# server's suggested delay is cheaper than dropping the ad.
GEMINI_MAX_BACKOFF_SEC = max(1.0, _env_float("GEMINI_MAX_BACKOFF_SEC", 65.0))

ENABLE_EXTERNAL_LOOKUPS = _env_bool("ENABLE_EXTERNAL_LOOKUPS", True)

# Brave Search grounds the world-price and Notebookcheck lookups in real
# results instead of asking Gemini to recall a price. Free tier: 1 request per
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
ADS_ANALYZE_LIMIT = max(1, _env_int("ADS_ANALYZE_LIMIT", 500))

PASSMARK_CACHE_DAYS = max(1, _env_int("PASSMARK_CACHE_DAYS", 7))
WORLD_PRICE_TOP_N = max(0, _env_int("WORLD_PRICE_TOP_N", 10))

DB_NAME = os.getenv("DB_NAME", "laptops_database.db")
