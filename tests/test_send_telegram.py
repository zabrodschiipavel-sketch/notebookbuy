"""Tests for the Telegram digest: deal selection, cache lookup, formatting."""
from estimation import external_cache_key
from send_telegram import apply_ai_review, format_deal, process_deals


COMPONENTS = {
    "base_laptop_price": 200,
    "cpu_tiers": {
        "i7": {"price": 200, "score": 70},
        "i5": {"price": 150, "score": 60},
    },
    "gpu_tiers": {
        "rtx 3060": {"price": 300, "score": 65},
        "integrated": {"price": 0, "score": 10},
    },
}


def make_row(**overrides) -> dict:
    """A strong laptop that comfortably clears MIN_VALUE_SCORE."""
    row = {
        "id": 1,
        "title": "Lenovo Legion 5",
        "price": 5000,
        "url": "https://999.md/ru/1",
        "description": "[Region: Bălți]",
        "cpu": "i7-12700h",
        "gpu": "rtx 3060",
        "ram": 16,
        "ssd": 512,
        "is_broken": 0,
        "year_est": 2023,
        "cpu_score": 20000,
        "gpu_score": 10000,
    }
    row.update(overrides)
    return row


def test_world_price_cache_hit_uses_component_key():
    """The analyzer keys its caches by CPU+GPU+RAM; the digest must read them
    with the same key (it used to look up by ad id and always miss)."""
    row = make_row()
    key = external_cache_key(row["cpu"], row["gpu"], row["ram"])
    price_cache = {key: {"current_usd": 1000}}  # 18 000 MDL at the fixed rate
    nbc_cache = {key: {"score": 87}}

    deals = process_deals([row], price_cache, nbc_cache, COMPONENTS)

    assert len(deals) == 1
    assert deals[0]["vs_str"] == "-72%"  # (5000 - 18000) / 18000
    assert deals[0]["nbc_score"] == 87


def test_missing_year_est_does_not_crash():
    row = make_row(year_est=None, price=1500)
    deals = process_deals([row], {}, {}, COMPONENTS)
    assert len(deals) == 1
    assert "запчасти" not in deals[0]["risk"]


def test_cheap_modern_apple_flags_mdm_risk():
    row = make_row(
        title="MacBook Air M2 2023",
        cpu="apple m2",
        gpu="integrated",
        cpu_score=35000,
        gpu_score=0,
        price=11000,
        year_est=2024,
    )
    deals = process_deals([row], {}, {}, COMPONENTS)
    assert len(deals) == 1
    assert deals[0]["brand"] == "Apple"
    assert "MDM" in deals[0]["risk"]


def test_parts_and_buying_ads_are_skipped():
    rows = [
        make_row(id=1, title="Lenovo Legion 5 piese"),
        make_row(id=2, title="Куплю ноутбук дорого"),
        make_row(id=3),
    ]
    deals = process_deals(rows, {}, {}, COMPONENTS)
    assert [d["url"] for d in deals] == ["https://999.md/ru/1"]


def test_region_parsed_from_spec_tag():
    deals = process_deals([make_row()], {}, {}, COMPONENTS)
    assert deals[0]["region"] == "Бельцы"


def test_format_deal_escapes_html_in_title():
    deal = {
        "title": "Ноутбук <b>50% *скидка*</b> & подарок",
        "price": 5000,
        "url": "https://999.md/ru/1",
        "value_score": 150.0,
        "vs_str": "-20%",
        "nbc_score": 80,
        "cpu": "i7-12700h",
        "ram": 16,
        "ssd": 512,
        "brand": "LENOVO",
        "risk": "",
        "region": "Бельцы",
    }
    text = format_deal(1, deal)
    assert "&lt;b&gt;50% *скидка*&lt;/b&gt; &amp; подарок" in text
    assert 'href="https://999.md/ru/1"' in text


def test_apply_ai_review_excludes_and_annotates():
    deals = [
        {"id": 1, "title": "Maci Brook pro", "value_score": 200.0},
        {"id": 2, "title": "Lenovo Legion 5", "value_score": 150.0},
        {"id": 3, "title": "No review for this one", "value_score": 120.0},
    ]
    reviews = {
        "1": {"verdict": "exclude", "reason": "цена в 30 раз ниже рынка, скам"},
        "2": {"verdict": "great", "reason": "спеки согласованы, честная цена"},
    }
    result = apply_ai_review(deals, reviews)
    assert [d["id"] for d in result] == [2, 3]
    assert result[0]["ai_note"] == "✅ спеки согласованы, честная цена"
    assert "ai_note" not in result[1]


def test_apply_ai_review_empty_reviews_is_noop():
    deals = [{"id": 1, "title": "x", "value_score": 150.0}]
    assert apply_ai_review(deals, {}) is deals


def test_format_deal_renders_ai_note():
    deal = {
        "title": "Lenovo", "price": 5000, "url": "https://999.md/ru/1",
        "value_score": 150.0, "vs_str": "-20%", "nbc_score": 80,
        "cpu": "i7", "ram": 16, "ssd": 512, "brand": "LENOVO",
        "risk": "", "region": "Кишинёв",
        "ai_note": "⚠️ проверьте, продаётся ли сам ноутбук",
    }
    text = format_deal(1, deal)
    assert "Gemini:" in text
    assert "проверьте, продаётся ли сам ноутбук" in text


def test_process_deals_includes_id_and_description():
    deals = process_deals([make_row()], {}, {}, COMPONENTS)
    assert deals[0]["id"] == 1
    assert deals[0]["description"] == "[Region: Bălți]"


def test_format_deal_region_only_in_moldova_section():
    deal = {
        "title": "Lenovo", "price": 5000, "url": "https://999.md/ru/1",
        "value_score": 150.0, "vs_str": "-20%", "nbc_score": 80,
        "cpu": "i7", "ram": 16, "ssd": 512, "brand": "LENOVO",
        "risk": "⚠️ Подозрительно дешево", "region": "Кишинёв",
    }
    assert "Регион" in format_deal(1, deal, show_region=True)
    assert "Регион" not in format_deal(1, deal, show_region=False)
    assert "РИСК" in format_deal(1, deal)
