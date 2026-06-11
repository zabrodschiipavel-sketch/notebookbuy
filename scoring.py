"""
Shared laptop business logic.

Keep scoring, category rules, display normalization, and cache version here so
the analyzer, dashboard, and bundled app cannot drift apart.
"""
import datetime
import re


ANALYSIS_VERSION = "2026-06-11.1" # Parser fixes: RAM vs SSD sizes, M-chip title guard, warranty years, G7 CPU suffixes


MIN_PRICE_MDL = 500
MAX_PRICE_MDL = 15000
MIN_YEAR = 2014
MIN_CPU_SCORE = 2500
MIN_ACCEPTABLE_TECH_PTS = 1000 # New threshold for tech_pts to prevent overvaluing very low-end devices

RAM_MULT = {
    4: 0.6,
    6: 0.7,
    8: 0.8,
    12: 0.9,
    16: 1.0,
    24: 1.08,
    32: 1.15,
    48: 1.18,
    64: 1.2,
}

CATEGORY_EMOJI = {
    "Gaming": "[GAME]",
    "MacBook": "[MAC]",
    "Ultrabook": "[ULTRA]",
    "Office": "[OFFICE]",
}

CATEGORY_LABEL = {
    "Gaming": "GAMING",
    "MacBook": "MACBOOK",
    "Ultrabook": "ULTRABOOKS",
    "Office": "OFFICE",
}

_BUYING_AD_RE = re.compile(
    r"\b(?:куплю|скуплю|скупка|выкуп|cumpăr|cumpar|cumpărăm|cumparam)\b",
    re.IGNORECASE,
)

SHOP_SPAM_KEYWORDS = (
    "cele mai bune preturi",
    "cele mai bune prețuri",
    "pentru toate laptopurile",
    "asortiment",
)


def is_unwanted_ad(title: str, description: str = "") -> bool:
    """True for buying requests ('куплю/cumpăr') and shop spam, not real sales."""
    blob = f"{str(title or '').lower()} {str(description or '').lower()}"
    if _BUYING_AD_RE.search(blob):
        return True
    return any(kw in blob for kw in SHOP_SPAM_KEYWORDS)


# --- Fallback Tiers and Estimation Logic ---
MDL_USD_RATE = 18.0

# The CPU_TIERS, GPU_TIERS, get_cpu_tier_info, get_gpu_tier_info,
# estimate_fallback_price, and estimate_fallback_score functions
# have been moved to laptop_analyzer_v3.py to use components_db.json.
# These are no longer needed here.
# --- End Fallback Tiers and Estimation Logic ---


def infer_ssd_gb(ssd: int | None, year_est: int | None, is_apple: bool = False) -> int:
    """Single source of truth for the "missing SSD" heuristic.

    If an SSD size was detected, return it unchanged. Otherwise, for devices
    from 2021 onward assume a sensible default (256 GB for Apple, 512 GB for
    the rest); older/unknown devices stay at 0.
    """
    if ssd and ssd > 0:
        return ssd
    if year_est and year_est >= 2021:
        return 256 if is_apple else 512
    return 0


def estimate_year_from_cpu(cpu_name: str) -> int | None:
    if not cpu_name:
        return None

    cpu_name = str(cpu_name).lower()
    current_year = datetime.datetime.now().year

    # Intel Core i-series
    intel_gen_years = {
        1: 2009, 2: 2011, 3: 2012, 4: 2013, 5: 2015, 6: 2015, 7: 2016, 8: 2017,
        9: 2018, 10: 2019, 11: 2021, 12: 2022, 13: 2023, 14: 2024
    }
    intel_match = re.search(r"i[3579][-\s](\d{4,5})", cpu_name)
    if intel_match:
        model_num = intel_match.group(1)
        gen = None
        if len(model_num) == 5: # e.g., 12700H -> 12th gen
            gen = int(model_num[:2])
        elif len(model_num) == 4: # e.g., 8550U -> 8th gen, 10210U -> 10th gen
            if model_num.startswith(('10', '11')): # 10th and 11th gen use 2-digit prefix
                gen = int(model_num[:2])
            else: # Older gens use 1-digit prefix
                gen = int(model_num[0])
        if gen and gen in intel_gen_years:
            return intel_gen_years[gen]

    # Intel Core Ultra
    ultra_match = re.search(r"core\sultra\s[3579]\s(\d{3})", cpu_name)
    if ultra_match:
        return 2024 # Core Ultra launched late 2023, widely available 2024

    # AMD Ryzen
    amd_gen_years = {
        1: 2017, 2: 2018, 3: 2019, 4: 2020, 5: 2021, 6: 2022, 7: 2023, 8: 2024
    }
    amd_match = re.search(r"ryzen\s+[3579]\s+(\d)", cpu_name) # Captures first digit of 4-digit model
    if amd_match:
        gen = int(amd_match.group(1))
        if gen in amd_gen_years:
            return amd_gen_years[gen]

    # Apple M-series
    for chip, year in {"m1": 2020, "m2": 2022, "m3": 2023, "m4": 2024}.items():
        if chip in cpu_name:
            return year

    # Snapdragon
    if "snapdragon" in cpu_name:
        return 2024 # Assuming recent Snapdragon X Elite

    # Fallback for Celeron/Pentium or unidentifiable CPUs
    if "celeron" in cpu_name or "pentium" in cpu_name:
        return 2019 # General estimate for relevant budget CPUs

    # Default to 7 years old if no specific year can be estimated, to apply some age penalty.
    return current_year - 7


def normalize_cpu_name(name: str, max_len: int = 25) -> str:
    if not name:
        return ""

    n = str(name).strip()
    if not n:
        return ""

    n_low = n.lower()
    if n[0].isupper() and len(n) > 6:
        return n[:max_len]
    if re.match(r"i[3579]-", n_low):
        return ("Core " + n.upper())[:max_len]
    if re.match(r"(ryzen|core ultra)", n_low):
        return n.title()[:max_len]
    return n[:max_len]


def classify_laptop(cpu: str, gpu_score: int, price: int | float) -> str:
    cpu_l = str(cpu or "").lower()
    gpu_score = gpu_score or 0
    price = price or 0

    if any(chip in cpu_l for chip in ("m1", "m2", "m3", "m4")):
        return "MacBook"
    if gpu_score > 6500:
        return "Gaming"
    if re.search(r"i[3579][-\s]\d{4,5}hx", cpu_l):
        return "Gaming"
    if re.search(r"\d{4}hs\b", cpu_l) and price > 10000:
        return "Gaming"
    if re.search(r"i[35][-\s]\d{4,5}[ug]\b", cpu_l):
        return "Office"
    if re.search(r"ryzen\s[35]\s\d{4}[ug]\b", cpu_l):
        return "Office"
    if gpu_score == 0 and price < 7000:
        return "Office"

    return "Ultrabook"


def score_laptop(
    cpu_score: int,
    gpu_score: int,
    ram: int,
    ssd: int,
    year_est: int | None,
    price: int | float,
    is_broken: bool,
) -> dict:
    """
    Returns dict with keys: value_score, tech_pts.
    value_score = performance-per-MDL index; higher means better value.
    tech_pts = hardware quality, ignoring price.
    """
    current_year = datetime.datetime.now().year

    cpu_val = cpu_score or 0
    gpu_val = gpu_score or 0
    ram_val = ram
    ssd_val = ssd
    year = year_est or (current_year - 7) # Default to 7 years old if year not estimated

    # SSD heuristic (shared with parser/telegram): assume a default if missing.
    ssd_val = infer_ssd_gb(ssd_val, year)

    # Round price to nearest integer
    effective_price = round(price)
    if effective_price <= 0: # Ensure price is positive to avoid division by zero or negative scores
        effective_price = 1

    # RAM multiplier - penalize 0 RAM significantly
    ram_m = RAM_MULT.get(ram_val, 0.05 if ram_val == 0 else (0.6 if ram_val < 8 else 1.2))
    # SSD bonus - penalize 0 SSD
    ssd_bonus = min(ssd_val * 2, 4000) # Max 4000 points for SSD

    # Age penalty: more aggressive for older devices, lower floor
    age = max(0, current_year - year)
    age_penalty = max(0.05, 1.0 - age * 0.12) # 12% per year, min 5% score retention

    broken_m = 0.05 if is_broken else 1.0 # Much heavier penalty for broken/scam laptops

    # Base technical points calculation
    tech_base = (cpu_val * 0.55) + (gpu_val * 0.35) + (ram_val * 150 + ssd_bonus) * 0.10

    # Apply multipliers
    tech_pts = tech_base * ram_m * age_penalty * broken_m

    # Introduce a minimum tech_pts threshold to prevent very low-end devices from scoring high due to low price
    if tech_pts < MIN_ACCEPTABLE_TECH_PTS:
        # Quadratic penalty for very low tech_pts, making it harder for them to rank high
        tech_pts *= (tech_pts / MIN_ACCEPTABLE_TECH_PTS)**2

    value_score = (tech_pts / effective_price) * 100

    # Further penalize if CPU score is too low, regardless of other factors
    if cpu_val < MIN_CPU_SCORE:
        value_score *= 0.5 # Halve the score if CPU is too weak

    return {"value_score": value_score, "tech_pts": tech_pts}
