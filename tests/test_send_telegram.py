"""Tests for the Telegram digest: deal selection, cache lookup, formatting."""
from estimation import external_cache_key, plausible_nbc_score
from send_telegram import (
    TELEGRAM_MSG_LIMIT,
    annotate_with_history,
    apply_ai_review,
    clip_to_limit,
    dedupe_deals,
    format_deal,
    process_deals,
    split_message,
    update_history,
)


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


def _deal(id_, title, cpu="i7-12700h", ram=16, ssd=512, price=5000, score=150.0):
    return {"id": id_, "title": title, "cpu": cpu, "ram": ram, "ssd": ssd,
            "price": price, "value_score": score}


def test_dedupe_collapses_reposted_listings():
    deals = [
        _deal(1, "Lenovo Legion 5 Pro 16 RTX 3060", score=180.0),
        _deal(2, "Lenovo Legion 5 Pro RTX 3060 16'' (как новый)", score=170.0),
        _deal(3, "Dell XPS 15", cpu="i7-13700h", score=160.0),
    ]
    result = dedupe_deals(deals)
    assert [d["id"] for d in result] == [1, 3]  # best-ranked copy survives


def test_dedupe_keeps_same_model_with_different_specs():
    deals = [
        _deal(1, "MacBook Air M2", cpu="m2", ram=8, ssd=256),
        _deal(2, "MacBook Air M2", cpu="m2", ram=16, ssd=512),
    ]
    assert len(dedupe_deals(deals)) == 2


def test_history_annotation_new_and_repeat():
    history = {"2": {"first_seen": "2026-06-09", "last_seen": "2026-06-10", "price": 6000}}
    deals = [_deal(1, "New laptop"), _deal(2, "Old laptop", price=5000)]
    annotate_with_history(deals, history, "2026-06-11")
    assert deals[0]["seen_note"] == "🆕 Впервые в топе"
    assert "В топе с 09.06" in deals[1]["seen_note"]
    assert "6,000 → 5,000" in deals[1]["seen_note"]  # price drop since last seen


def test_history_same_day_repeat_is_silent():
    history = {"1": {"first_seen": "2026-06-11", "last_seen": "2026-06-11", "price": 5000}}
    deals = [_deal(1, "Laptop")]
    annotate_with_history(deals, history, "2026-06-11")
    assert deals[0]["seen_note"] == ""


def test_update_history_preserves_first_seen():
    history = {"1": {"first_seen": "2026-06-01", "last_seen": "2026-06-10", "price": 6000}}
    history = update_history(history, [_deal(1, "Laptop", price=5500)], "2026-06-11")
    assert history["1"]["first_seen"] == "2026-06-01"
    assert history["1"]["price"] == 5500


def test_plausible_nbc_score():
    assert plausible_nbc_score(80) == 80
    assert plausible_nbc_score("85") == 85
    assert plausible_nbc_score(12) is None   # the hallucinated low scores
    assert plausible_nbc_score(100) is None
    assert plausible_nbc_score(None) is None
    assert plausible_nbc_score("n/a") is None


def test_split_message_single_when_fits():
    parts = split_message("header\n", ["sect1\n", "sect2\n"])
    assert parts == ["header\nsect1\nsect2\n"]


def test_split_message_splits_by_section_when_too_long():
    big1 = "deal one\n\n" * 300   # ~3000 chars
    big2 = "deal two\n\n" * 300
    parts = split_message("header\n", [big1, big2])
    assert len(parts) == 2
    assert parts[0].startswith("header\n")
    assert all(len(p) <= TELEGRAM_MSG_LIMIT for p in parts)


def test_clip_to_limit_cuts_at_deal_boundary():
    text = ("x" * 100 + "\n\n") * 50  # > 4096
    clipped = clip_to_limit(text)
    assert len(clipped) <= TELEGRAM_MSG_LIMIT
    assert clipped.endswith("x")  # cut exactly at a \n\n boundary


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
