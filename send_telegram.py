import html
import json
import logging
import os
import re
import sqlite3
from datetime import datetime

import requests
from rapidfuzz import fuzz

# Import shared configurations and scoring
from app_config import AI_REVIEW_TOP_N, DB_NAME, ENABLE_AI_REVIEW
from estimation import (
    estimate_fallback_price,
    estimate_fallback_score,
    external_cache_key,
    plausible_nbc_score,
)
from scoring import MDL_USD_RATE, infer_ssd_gb, is_unwanted_ad, score_laptop


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Minimum value_score for a laptop to be included in the Telegram digest.
MIN_VALUE_SCORE = 100

# Telegram rejects messages longer than 4096 chars with a 400 — the digest
# must be split/clipped, not dropped.
TELEGRAM_MSG_LIMIT = 4096

# Retrieve tokens from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WORLD_PRICE_CACHE = "pricehistory_cache.json"
NBC_CACHE_FILE = "notebookcheck_cache.json"
COMPONENTS_DB_FILE = "components_db.json"
# Which ads already appeared in previous digests (persisted between runs).
DIGEST_HISTORY_FILE = "digest_history.json"


def load_json_cache(filename):
    if os.path.exists(filename):
        try:
            with open(filename, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def extract_brand(title):
    title_lower = title.lower()
    brands = ["lenovo", "asus", "hp", "apple", "macbook", "dell", "acer", "msi", "gigabyte", "samsung", "huawei", "xiaomi", "microsoft", "razer", "toshiba", "sony"]
    for b in brands:
        if b in title_lower:
            return "Apple" if b in ("apple", "macbook") else b.upper()
    return "OTHER"

def extract_region(description):
    if not description:
        return "Молдова"
    m = re.search(r'\[region:\s*([^\]]+)\]', description, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        val_lower = val.lower()
        if "chișinău" in val_lower or "chisinau" in val_lower or "кишин" in val_lower:
            return "Кишинёв"
        elif "bălți" in val_lower or "balti" in val_lower or "бельц" in val_lower:
            return "Бельцы"
        return val

    # Fallback search in raw text
    desc_lower = description.lower()
    if "бельц" in desc_lower or "bălți" in desc_lower or "balti" in desc_lower or "бэлць" in desc_lower:
        return "Бельцы"
    if "кишин" in desc_lower or "chișinău" in desc_lower or "chisinau" in desc_lower:
        return "Кишинёв"
    return "Молдова"


def _get_runtime_ssd(r, brand, title_lower):
    is_apple = brand == 'Apple' or any(w in title_lower for w in ['apple', 'macbook'])
    year_est = r['year_est'] or (datetime.now().year - 7)
    return infer_ssd_gb(r['ssd'], year_est, is_apple)


def process_deals(rows, price_cache, nbc_cache, components_data):
    parts_keywords = ["defect", "piese", "запчасти"]
    deals = []

    for r in rows:
        title = r['title']
        title_lower = title.lower()
        description = r['description'] if 'description' in r.keys() else ''
        if any(kw in title_lower for kw in parts_keywords):
            continue

        # Skip buying requests ("куплю/cumpăr") and shop spam
        if is_unwanted_ad(title, description):
            continue

        brand = extract_brand(title)
        # Smart Normalization
        if any(w in title_lower for w in ['mackbook', 'macbook', 'apple', 'mac']):
            brand = 'Apple'

        # Score laptop
        score_res = score_laptop(
            cpu_score=r['cpu_score'],
            gpu_score=r['gpu_score'],
            ram=r['ram'],
            ssd=r['ssd'],
            year_est=r['year_est'],
            price=r['price'],
            is_broken=r['is_broken']
        )
        value_score = round(score_res.get("value_score", 0), 1)

        # vs World calculation — caches are keyed by CPU+GPU+RAM (see analyzer)
        cache_key = external_cache_key(r['cpu'], r['gpu'], r['ram'])
        vs_pct = 0.0
        vs_str = "—"

        # Check cache
        cache_data = price_cache.get(cache_key, {})
        world_price_usd = cache_data.get('current_usd')

        if not world_price_usd:
            # Fallback
            calc_price_mdl = estimate_fallback_price(r['cpu'], r['gpu'], r['ram'], r['ssd'], brand, components_data)
            if calc_price_mdl > 0:
                vs_pct = ((r['price'] - calc_price_mdl) / calc_price_mdl) * 100
                vs_str = f"{int(round(vs_pct))}%"
        else:
            world_price_mdl = world_price_usd * MDL_USD_RATE
            if world_price_mdl > 0:
                vs_pct = ((r['price'] - world_price_mdl) / world_price_mdl) * 100
                vs_str = f"{int(round(vs_pct))}%"

        # NBC Score — only a plausible Notebookcheck rating is trusted; the
        # AI search hallucinates values like 12% that flip to 80% a run later.
        nbc_data = nbc_cache.get(cache_key, {})
        nbc_score = plausible_nbc_score(nbc_data.get('score'))
        if not nbc_score:
            nbc_score = estimate_fallback_score(r['cpu'], r['gpu'], r['ram'], components_data)

        # Risk assessment
        risk = ""
        if brand == 'Apple' and r['price'] < 12000 and any(chip in title_lower or chip in str(r['cpu']).lower() for chip in ['m2', 'm3', 'm4']):
            risk = "⚠️ Слишком низкая цена! Проверяйте на MDM профиль и iCloud!"
        elif brand == 'Apple' and vs_pct < -55:
            risk = "⚠️ Высокий (Скам/Блок)"
        elif brand != 'Apple' and vs_pct < -65:
            risk = "⚠️ Подозрительно дешево"
        elif r['price'] < 2000 and (r['year_est'] or 0) > 2019:
            risk = "⚠️ На запчасти?"

        # Smart runtime SSD fallback for display/processing
        ssd_val = _get_runtime_ssd(r, brand, title_lower)

        # We want to filter for good value_score
        if value_score >= MIN_VALUE_SCORE:
            deals.append({
                'id': r['id'],
                'title': title,
                'price': int(r['price']),
                'url': r['url'],
                'value_score': value_score,
                'vs_str': vs_str,
                'nbc_score': nbc_score,
                'cpu': r['cpu'],
                'ram': r['ram'],
                'ssd': ssd_val,
                'brand': brand,
                'risk': risk,
                'region': extract_region(description),
                'description': description or '',
            })
    return deals


def dedupe_deals(deals: list[dict]) -> list[dict]:
    """Collapse re-posted listings: same parsed specs + similar title.

    Sellers re-post the same laptop under new ad ids; with identical
    CPU/RAM/SSD and a fuzzy-matching title only the best-ranked copy stays
    (callers pass deals sorted by value_score descending).
    """
    kept: list[dict] = []
    for deal in deals:
        is_dup = any(
            str(deal['cpu']).lower() == str(k['cpu']).lower()
            and deal['ram'] == k['ram']
            and deal['ssd'] == k['ssd']
            and fuzz.token_set_ratio(deal['title'].lower(), k['title'].lower()) >= 85
            for k in kept
        )
        if not is_dup:
            kept.append(deal)
    return kept


def _short_date(iso_date: str) -> str:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m")
    except ValueError:
        return iso_date


def annotate_with_history(deals: list[dict], history: dict, today: str) -> None:
    """Mark each deal as new or repeated (with price delta) vs. past digests."""
    for deal in deals:
        prev = history.get(str(deal.get("id")))
        if not prev:
            deal["seen_note"] = "🆕 Впервые в топе"
            continue
        if prev.get("first_seen") == today:
            deal["seen_note"] = ""  # first appeared earlier today — skip the noise
            continue
        note = f"🔁 В топе с {_short_date(prev.get('first_seen', '?'))}"
        old_price = prev.get("price") or 0
        delta = deal["price"] - old_price
        if old_price and abs(delta) / old_price > 0.01:
            arrow = "📉" if delta < 0 else "📈"
            note += f", цена {arrow} {old_price:,} → {deal['price']:,} MDL"
        deal["seen_note"] = note


def update_history(history: dict, sent_deals: list[dict], today: str) -> dict:
    """Record sent deals; keep entries fresh enough to matter (45 days)."""
    for deal in sent_deals:
        key = str(deal.get("id"))
        prev = history.get(key, {})
        history[key] = {
            "first_seen": prev.get("first_seen", today),
            "last_seen": today,
            "price": deal["price"],
        }
    cutoff = sorted({v.get("last_seen", "") for v in history.values()}, reverse=True)[:45]
    keep_dates = set(cutoff)
    return {k: v for k, v in history.items() if v.get("last_seen", "") in keep_dates}


def apply_ai_review(deals: list[dict], reviews: dict[str, dict]) -> list[dict]:
    """Apply Gemini Pro verdicts to the deal list.

    'exclude' drops the deal entirely; other verdicts attach a short note that
    format_deal renders. Deals without a review pass through unchanged, so an
    empty review dict (AI disabled/failed) leaves the digest as-is.
    """
    if not reviews:
        return deals

    note_emoji = {"great": "✅", "ok": "👌", "suspicious": "⚠️"}
    kept = []
    for deal in deals:
        review = reviews.get(str(deal.get("id")))
        if not review:
            kept.append(deal)
            continue
        verdict = str(review.get("verdict", "")).lower()
        reason = str(review.get("reason", "")).strip()
        if verdict == "exclude":
            log.info("AI review excluded: %s (%s)", deal["title"][:40], reason)
            continue
        if reason:
            deal = {**deal, "ai_note": f"{note_emoji.get(verdict, '🤖')} {reason}"}
        kept.append(deal)
    return kept


def clip_to_limit(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> str:
    """Trim an over-long message at a deal boundary so HTML tags stay intact."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n\n", 0, limit)
    return text[:cut] if cut > 0 else text[:limit]


def split_message(header: str, sections: list[str], limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """One message when everything fits, otherwise one message per section."""
    combined = header + "".join(sections)
    if len(combined) <= limit:
        return [combined]
    parts = [header + sections[0]] + sections[1:]
    return [clip_to_limit(p, limit) for p in parts]


def format_deal(idx: int, deal: dict, show_region: bool = True) -> str:
    """Render one deal as Telegram HTML. User-supplied text is escaped so
    listing titles with *, _, [, < etc. cannot break the markup."""
    brand_emoji = "🍏" if deal['brand'] == "Apple" else "💻"
    title = html.escape(deal['title'][:80])
    specs = html.escape(f"{deal['cpu']} | {deal['ram']}GB RAM | {deal['ssd']}GB SSD")
    lines = [f"{idx}. {brand_emoji} <b>{title}</b>"]
    if show_region:
        lines.append(f" 📍 <b>Регион:</b> <code>{html.escape(deal['region'])}</code>")
    lines += [
        f" 💰 <b>Цена:</b> {deal['price']:,} MDL",
        f" 📈 <b>Выгода:</b> <code>{html.escape(deal['vs_str'])} vs World</code>",
        f" 🎯 <b>NBC Score:</b> <code>{deal['nbc_score']}%</code> | <b>Value Score:</b> <code>{deal['value_score']}</code>",
        f" 🛠 <b>Характеристики:</b> <code>{specs}</code>",
    ]
    if deal.get('risk'):
        lines.append(f" 🚨 <b>РИСК:</b> <code>{html.escape(deal['risk'])}</code>")
    if deal.get('ai_note'):
        lines.append(f" 🤖 <b>Gemini:</b> <i>{html.escape(deal['ai_note'])}</i>")
    if deal.get('seen_note'):
        lines.append(f" {html.escape(deal['seen_note'])}")
    lines.append(f" 🔗 <a href=\"{html.escape(deal['url'], quote=True)}\">Открыть объявление</a>")
    return "\n".join(lines) + "\n\n"


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram configuration missing. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        return

    log.info("Connecting to database and calculating the best deals...")
    if not os.path.exists(DB_NAME):
        log.error("Database %s not found. Cannot send notifications.", DB_NAME)
        return

    # Load caches
    price_cache = load_json_cache(WORLD_PRICE_CACHE)
    nbc_cache = load_json_cache(NBC_CACHE_FILE)
    components_data = load_json_cache(COMPONENTS_DB_FILE)

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT a.id, a.title, a.price, a.url, a.description, c.cpu, c.gpu, c.ram, c.ssd, c.is_broken, c.year_est, c.cpu_score, c.gpu_score
        FROM ads a
        JOIN analysis_cache c ON a.id = c.id
        WHERE a.parsed_at >= datetime(substr((SELECT MAX(parsed_at) FROM ads), 1, 19), '-2 hours')
    """
    rows = cursor.execute(query).fetchall()
    conn.close()

    deals = process_deals(rows, price_cache, nbc_cache, components_data)

    if not deals:
        log.info("No high-value laptop deals found today.")
        return

    deals.sort(key=lambda x: x['value_score'], reverse=True)

    before = len(deals)
    deals = dedupe_deals(deals)
    if len(deals) < before:
        log.info("Deduplicated re-posted listings: %d -> %d", before, len(deals))

    # Second-pass review of the top candidates with Gemini Pro: drops scam
    # listings and parsing garbage that the regex/score pipeline lets through.
    if ENABLE_AI_REVIEW and AI_REVIEW_TOP_N > 0:
        # Imported lazily: pulls google-genai, needed only when review is on.
        from ai_service import AIService

        candidates = deals[:AI_REVIEW_TOP_N]
        log.info("Reviewing top %d deals with Gemini Pro...", len(candidates))
        reviews = AIService().review_deals(candidates)
        if reviews:
            deals = apply_ai_review(deals, reviews)
            log.info("AI review applied: %d deals remain", len(deals))

    if not deals:
        log.info("All candidate deals were rejected by AI review; nothing to send.")
        return

    # 1. Moldova deals (all regions) - Top 5
    moldova_deals = deals[:5]

    # 2. Balti deals - Top 5
    balti_deals = [d for d in deals if d['region'] == "Бельцы"][:5]

    # Mark deals already shown in previous digests (and price moves since).
    history = load_json_cache(DIGEST_HISTORY_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    annotate_with_history(moldova_deals + balti_deals, history, today)

    # Format beautiful message (Telegram HTML — robust to special chars in titles)
    header = "🔥 <b>ТОП ВЫГОДНЫХ НОУТБУКОВ 999.MD</b> 🔥\n"
    header += f"📅 <i>Дата отчета: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>\n\n"

    # Moldova Section
    sect_moldova = "🌍 <b>ВСЯ МОЛДОВА (ТОП-5)</b>\n"
    sect_moldova += "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    for idx, d in enumerate(moldova_deals, start=1):
        sect_moldova += format_deal(idx, d, show_region=True)

    # Balti Section
    sect_balti = "\n🔔 <b>БЕЛЬЦЫ (ТОП-5)</b>\n"
    sect_balti += "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if balti_deals:
        for idx, d in enumerate(balti_deals, start=1):
            sect_balti += format_deal(idx, d, show_region=False)
    else:
        sect_balti += "   <i>Выгодных предложений в Бельцах пока не найдено.</i>\n\n"

    parts = split_message(header, [sect_moldova, sect_balti])
    log.info("Sending %d message(s) to Telegram...", len(parts))
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    sent_any = False
    for part in parts:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                log.info("Telegram notification sent successfully!")
                sent_any = True
            else:
                log.error("Failed to send message: %s", response.text)
        except Exception as e:
            log.error("Error sending Telegram message: %s", e)

    if sent_any:
        history = update_history(history, moldova_deals + balti_deals, today)
        with open(DIGEST_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
