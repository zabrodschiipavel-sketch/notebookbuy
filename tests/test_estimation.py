"""Tests for the shared component-based fallback estimation."""
from estimation import estimate_fallback_price, estimate_fallback_score


COMPONENTS = {
    "base_laptop_price": 200,
    "cpu_tiers": {
        "i7": {"price": 200, "score": 70},
        "i5": {"price": 150, "score": 60},
    },
    "gpu_tiers": {
        "rtx 4070": {"price": 500, "score": 80},
        "integrated": {"price": 0, "score": 10},
    },
}


def test_price_caps_ram_and_ssd():
    """RAM is capped at 16 GB and SSD at 512 GB so big configs don't skew price."""
    capped = estimate_fallback_price("i5", "integrated", 16, 512, components=COMPONENTS)
    oversized = estimate_fallback_price("i5", "integrated", 64, 4096, components=COMPONENTS)
    smaller = estimate_fallback_price("i5", "integrated", 8, 256, components=COMPONENTS)
    assert oversized == capped
    assert smaller < capped


def test_price_apple_premium():
    base = estimate_fallback_price("i7", "integrated", 16, 512, components=COMPONENTS)
    apple = estimate_fallback_price("i7", "integrated", 16, 512, brand="Apple", components=COMPONENTS)
    assert apple > base


def test_price_apple_detected_from_cpu():
    """M-series CPU implies Apple even when brand is not passed explicitly."""
    base = estimate_fallback_price("i7", "integrated", 16, 512, components=COMPONENTS)
    apple = estimate_fallback_price("apple m3", "integrated", 16, 512, components=COMPONENTS)
    # Apple gets +300 base; m3 isn't in COMPONENTS cpu_tiers so it uses the default
    # tier — the premium still makes it strictly pricier than the i7 baseline.
    assert apple > base


def test_price_zero_without_components():
    assert estimate_fallback_price("i7", "integrated", 16, 512, components={}) == 0


def test_score_apple_shortcut():
    assert estimate_fallback_score("Apple M3 Max", "integrated", 16) == 90
    assert estimate_fallback_score("Apple M1", "integrated", 8) == 80


def test_score_sums_cpu_gpu_and_clamps():
    # 70 (i7) + 80 (rtx 4070) = 150 -> clamped to 100
    assert estimate_fallback_score("i7", "rtx 4070", 8, components=COMPONENTS) == 100


def test_score_ram_bonus():
    low = estimate_fallback_score("i5", "integrated", 8, components=COMPONENTS)   # 60 + 10
    high = estimate_fallback_score("i5", "integrated", 16, components=COMPONENTS)  # + 3
    assert low == 70
    assert high == 73


def test_score_zero_without_components():
    assert estimate_fallback_score("i7", "rtx 4070", 8, components={}) == 0


def test_shipped_tiers_cover_current_hardware():
    """A part missing from components_db.json falls to the default tier: every
    i9 used to get CPU score 2 and an RTX 3060 counted as no GPU at all."""
    from estimation import (
        _DEFAULT_CPU_TIER,
        _DEFAULT_GPU_TIER,
        _tier_for,
        load_components_db,
    )

    db = load_components_db()
    for cpu in ("i9-13980hx", "m4", "ryzen ai 9 hx 370", "core ultra 5 125h", "ryzen 3 7320u"):
        assert _tier_for(cpu, db["cpu_tiers"], _DEFAULT_CPU_TIER) is not _DEFAULT_CPU_TIER, cpu
    for gpu in ("rtx 3060", "rtx 5070", "rtx 2060", "gtx 1660 ti"):
        assert _tier_for(gpu, db["gpu_tiers"], _DEFAULT_GPU_TIER) is not _DEFAULT_GPU_TIER, gpu
    # Longest keyword wins: a 5070 Ti is not priced as a plain 5070.
    assert _tier_for("rtx 5070 ti", db["gpu_tiers"], _DEFAULT_GPU_TIER) == db["gpu_tiers"]["rtx 5070 ti"]
