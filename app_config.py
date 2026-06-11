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

GEMINI_MAX_WORKERS = max(1, _env_int("GEMINI_MAX_WORKERS", 3))
GEMINI_REQUEST_DELAY_SEC = max(0.0, _env_float("GEMINI_REQUEST_DELAY_SEC", 0.5))
GEMINI_SEARCH_DELAY_SEC = max(0.0, _env_float("GEMINI_SEARCH_DELAY_SEC", 1.5))
GEMINI_MAX_RETRIES = max(1, _env_int("GEMINI_MAX_RETRIES", 3))

ENABLE_EXTERNAL_LOOKUPS = _env_bool("ENABLE_EXTERNAL_LOOKUPS", True)

MIN_CPU_SCORE = _env_int("MIN_CPU_SCORE", DEFAULT_MIN_CPU_SCORE)
ADS_ANALYZE_LIMIT = max(1, _env_int("ADS_ANALYZE_LIMIT", 500))

PASSMARK_CACHE_DAYS = max(1, _env_int("PASSMARK_CACHE_DAYS", 7))
WORLD_PRICE_TOP_N = max(0, _env_int("WORLD_PRICE_TOP_N", 10))

DB_NAME = os.getenv("DB_NAME", "laptops_database.db")
