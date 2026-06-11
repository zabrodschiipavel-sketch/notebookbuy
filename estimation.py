"""Single source of truth for component-based fallback estimation.

When external (Gemini-powered) world-price/review lookups are unavailable, the
analyzer and the Telegram notifier both need to *estimate* a synthetic world
price and a performance score from the parsed components. Keeping that logic
here prevents the analyzer report and the Telegram message from disagreeing on
the same laptop.
"""
import json
import logging

from scoring import MDL_USD_RATE


log = logging.getLogger(__name__)

COMPONENTS_DB_FILE = "components_db.json"

# Default tiers used when a component keyword is not found in components_db.json.
_DEFAULT_CPU_TIER = {"price": 100, "score": 2}
_DEFAULT_GPU_TIER = {"price": 0, "score": 0}

_components_cache: dict | None = None


def plausible_nbc_score(value) -> int | None:
    """Validate a Notebookcheck rating that came back from the AI web search.

    Real Notebookcheck verdicts live in roughly 50–95%; the search regularly
    hallucinates garbage like 12% or 15% (seen flipping to 80% for the same
    laptop a run later). Outside 40–99 we prefer the component formula.
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if 40 <= v <= 99 else None


def external_cache_key(cpu: str, gpu: str, ram: int) -> str:
    """Key for the world-price/NBC JSON caches.

    The analyzer writes these caches and the dashboard/notifier read them, so
    all three must build the key identically.
    """
    return f"{str(cpu or '')[:25]}_{str(gpu or '')[:15]}_{ram}"


def load_components_db(path: str = COMPONENTS_DB_FILE) -> dict:
    """Load (and memoize) component pricing/scoring data from JSON."""
    global _components_cache
    if _components_cache is None:
        try:
            with open(path, encoding="utf-8") as f:
                _components_cache = json.load(f)
            log.info("Loaded component data from %s", path)
        except FileNotFoundError:
            log.error("%s not found; fallback estimation will return zeros", path)
            _components_cache = {}
        except json.JSONDecodeError as e:
            log.error("Failed to parse %s: %s; fallback estimation disabled", path, e)
            _components_cache = {}
    return _components_cache


def _tier_for(name: str, tiers: dict, default: dict) -> dict:
    """Longest-keyword-first lookup so 'rtx 4070' beats a bare 'rtx' entry."""
    name_l = str(name or "").lower()
    for keyword in sorted(tiers, key=len, reverse=True):
        if keyword in name_l:
            return tiers[keyword]
    return default


def _is_apple(cpu: str, brand: str | None) -> bool:
    if brand and str(brand).lower() == "apple":
        return True
    return any(chip in str(cpu or "").lower() for chip in ("m1", "m2", "m3", "m4"))


def estimate_fallback_price(
    cpu: str,
    gpu: str,
    ram: int,
    ssd: int,
    brand: str | None = None,
    components: dict | None = None,
) -> int:
    """Estimate a synthetic world price (MDL) from parsed components."""
    components = components if components is not None else load_components_db()
    if not components:
        return 0

    base = components.get("base_laptop_price", 200)
    if _is_apple(cpu, brand):
        base += 300

    cpu_tier = _tier_for(cpu, components.get("cpu_tiers", {}), _DEFAULT_CPU_TIER)
    gpu_tier = _tier_for(gpu, components.get("gpu_tiers", {}), _DEFAULT_GPU_TIER)

    # Cap RAM/SSD so an unusually large config does not skew the baseline price.
    ram_gb = min(float(ram or 0), 16.0)
    ssd_gb = min(float(ssd) if ssd and ssd > 0 else 512.0, 512.0)

    total_usd = (
        base
        + cpu_tier.get("price", 0)
        + gpu_tier.get("price", 0)
        + ram_gb * 4
        + (ssd_gb / 128) * 10
    )
    return int(total_usd * MDL_USD_RATE)


def estimate_fallback_score(
    cpu: str,
    gpu: str,
    ram: int,
    components: dict | None = None,
) -> int:
    """Estimate a synthetic performance score (1-100) from parsed components."""
    if _is_apple(cpu, None):
        cpu_l = str(cpu).lower()
        return 90 if any(p in cpu_l for p in ("pro", "max", "ultra")) else 80

    components = components if components is not None else load_components_db()
    if not components:
        return 0

    cpu_tier = _tier_for(cpu, components.get("cpu_tiers", {}), _DEFAULT_CPU_TIER)
    gpu_tier = _tier_for(gpu, components.get("gpu_tiers", {}), _DEFAULT_GPU_TIER)

    score = cpu_tier.get("score", 0) + gpu_tier.get("score", 0)
    if float(ram or 0) >= 16:
        score += 3
    return int(min(100, max(1, score)))
