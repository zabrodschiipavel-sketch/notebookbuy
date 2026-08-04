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
# Spec extraction — the only path that makes tens of calls a day, so its
# limiting quota is requests-per-DAY, not per-minute. Pinned deliberately:
# free-tier RPD varies wildly inside the flash-lite family (3.1 and 3.5 get
# 500/day, 2.5 gets 20), and an alias gives no way to know which tier it bills
# against. A pinned model can be retired, but extraction now logs a
# parsed/failed tally and raises a CI annotation, so that failure is loud.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Model that sanity-checks the final top before it is sent. This used to
# default to gemini-pro-latest, whose free-tier quota is 0 — so every single
# run spent an attempt on a guaranteed failure before falling through to flash.
# Measured on real listings from past digests, full flash catches noticeably
# more garbage than flash-lite (3 of 4 planted scams vs 2), which is worth the
# few extra seconds on a once-a-day call.
# Set this to a pro model if the key ever gets billing enabled.
GEMINI_REVIEW_MODEL = os.getenv("GEMINI_REVIEW_MODEL", "gemini-flash-latest")
# Fallbacks survive a temporary 503 on the primary. An alias leads and a pinned
# model backs it up: the alias cannot go stale, the pin cannot be silently
# repointed. (The previous chain ended on gemini-2.5-flash, which now answers
# 404 "no longer available to new users" — a dead last resort.)
GEMINI_REVIEW_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_REVIEW_FALLBACK_MODEL", "gemini-3.6-flash,gemini-3.1-flash-lite"
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
# Requests per minute allowed *per model*, with headroom for clock skew.
# The free tier is not one number: flash-lite gets 15 RPM, full flash only 5.
# Throttling everything at the lite figure would 429 the review on every call.
GEMINI_RPM_LIMIT = max(1, _env_int("GEMINI_RPM_LIMIT", 12))        # lite, limit 15
GEMINI_RPM_LIMIT_FULL = max(1, _env_int("GEMINI_RPM_LIMIT_FULL", 4))  # full flash, limit 5
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
