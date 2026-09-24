import argparse
import datetime
import json
import logging
import os
import random
import re
import sqlite3
import sys
import time

import requests
from bs4 import BeautifulSoup  # Moved import to top for broader use

import run_summary
from app_config import DB_NAME, SCRAPE_MAX_ADS, SCRAPE_PAGE_DELAY_SEC, SCRAPE_PAGE_SIZE
from currency import eur_to_mdl, usd_to_mdl
from db import init_database
from retry_utils import call_with_retry


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ================= SETTINGS =================
CHECK_INTERVAL = 600  # 10 minutes
GRAPHQL_URL = "https://999.md/graphql"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Origin": "https://999.md",
    "Referer": "https://999.md/ru/list/computers-and-office-equipment/laptops",
    "Accept": "application/json",
}

# Load GraphQL query from file
QUERY_FILE = os.path.join(os.path.dirname(__file__), "query_999.graphql")
try:
    with open(QUERY_FILE, encoding="utf-8") as f:
        FULL_QUERY = f.read()
except Exception as e:
    log.error(f"Error loading GraphQL query: {e}")
    FULL_QUERY = ""

# ================= 1. DATABASE HELPERS =================
def get_current_price(cursor, ad_id: int) -> float | None:
    """Return the last known price for an ad, or None if not yet stored."""
    row = cursor.execute(
        'SELECT price FROM ads WHERE id = ?', (ad_id,)
    ).fetchone()
    return row[0] if row else None


def record_price(cursor, ad_id: int, price: float) -> None:
    """Append a price snapshot to price_history."""
    cursor.execute(
        'INSERT INTO price_history (ad_id, price, recorded_at) VALUES (?, ?, ?)',
        (ad_id, price, datetime.datetime.now())
    )


def get_price_history(ad_id: int, db: str = DB_NAME) -> list[dict]:
    """Return full price history for an ad as a list of {price, recorded_at} dicts."""
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            'SELECT price, recorded_at FROM price_history '
            'WHERE ad_id = ? ORDER BY recorded_at ASC',
            (ad_id,)
        ).fetchall()
    return [{"price": r[0], "recorded_at": r[1]} for r in rows]


def get_price_drops(min_drop_pct: float = 5.0, db: str = DB_NAME) -> list[dict]:
    """Find ads whose price dropped by at least *min_drop_pct* % since first seen."""
    with sqlite3.connect(db) as conn:
        rows = conn.execute('''
            SELECT
                a.id,
                a.title,
                a.url,
                MIN(ph.price)     AS min_price,
                MAX(ph.price)     AS max_price,
                ph_first.price    AS first_price,
                ph_last.price     AS last_price,
                COUNT(ph.id)      AS records
            FROM ads a
            JOIN price_history ph       ON ph.ad_id = a.id
            -- Anchored on the autoincrement id, not recorded_at: two snapshots
            -- taken inside the same clock tick share a timestamp, and then
            -- MIN(recorded_at) = MAX(recorded_at) matched both rows and the
            -- drop silently evaluated to 0%.
            JOIN (
                SELECT ad_id, price
                FROM price_history
                WHERE id IN (SELECT MIN(id) FROM price_history GROUP BY ad_id)
            ) ph_first ON ph_first.ad_id = a.id
            JOIN (
                SELECT ad_id, price
                FROM price_history
                WHERE id IN (SELECT MAX(id) FROM price_history GROUP BY ad_id)
            ) ph_last ON ph_last.ad_id = a.id
            GROUP BY a.id
            HAVING records > 1
               AND ph_first.price > 0
               AND (ph_first.price - ph_last.price) / ph_first.price * 100 >= ?
            ORDER BY (ph_first.price - ph_last.price) / ph_first.price DESC
        ''', (min_drop_pct,)).fetchall()

    result = []
    for row in rows:
        ad_id, title, url, mn, mx, first, last, records = row
        drop_pct = (first - last) / first * 100
        result.append({
            "ad_id":      ad_id,
            "title":      title,
            "url":        url,
            "first_price": first,
            "last_price":  last,
            "drop_pct":    round(drop_pct, 1),
            "records":     records,
        })
    return result


def save_or_update_ad(cursor, ad_data: dict) -> str:
    """Upsert an ad. Returns 'new' | 'price_drop' | 'price_rise' | 'unchanged'."""
    ad_id = ad_data['ID']
    new_price = ad_data['Цена']
    old_price = get_current_price(cursor, ad_id)

    if old_price is None:
        # New ad
        cursor.execute('''
            INSERT INTO ads (id, title, price, currency, url, description, image_url, parsed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            ad_id,
            ad_data['Заголовок'],
            new_price,
            ad_data['Валюта'],
            ad_data['Ссылка'],
            ad_data['HTML_страницы'],
            ad_data.get('Изображение'),
            datetime.datetime.now()
        ))
        record_price(cursor, ad_id, new_price)
        return "new"

    # Ad already exists — check for meaningful price change (>1% to filter FX noise)
    # Always update description and image_url, as parsing logic might have improved
    if abs(new_price - old_price) / max(old_price, 1) > 0.01:
        cursor.execute(
            'UPDATE ads SET price = ?, description = ?, image_url = ?, parsed_at = ? WHERE id = ?',
            (new_price, ad_data['HTML_страницы'], ad_data.get('Изображение'), datetime.datetime.now(), ad_id)
        )
        record_price(cursor, ad_id, new_price)
        return "price_drop" if new_price < old_price else "price_rise"
    else: # Price unchanged or changed insignificantly, but still update description and image
        cursor.execute(
            'UPDATE ads SET description = ?, image_url = ?, parsed_at = ? WHERE id = ?',
            (ad_data['HTML_страницы'], ad_data.get('Изображение'), datetime.datetime.now(), ad_id)
        )
        return "unchanged"


def _description_from_ad(ad: dict) -> str:
    """Extract listing description from GraphQL feature id 13.

    The feature value is usually a translations dict
    ({'ro': ..., 'ru': ..., 'translated': ...}); stringifying it whole used to
    store a Python-repr blob with literal "\\n" sequences that crippled the
    downstream regex parser.
    """
    desc_feature = ad.get("description")
    if isinstance(desc_feature, dict) and desc_feature.get("value"):
        val = desc_feature["value"]
        if isinstance(val, dict):
            val = val.get("translated") or val.get("ru") or val.get("ro") or ""
        return str(val)
    return ""


# Structured spec features exposed by the GraphQL API, appended to the body so
# the downstream regex parser can pick them up as [Label: value] tags.
_SPEC_FEATURES = [
    ("ssd_feature", "SSD"),
    ("hdd_type_feature", "HDD Type"),
    ("screen_size_feature", "Screen"),
    ("cpu_model_feature", "CPU Model"),
    ("gpu_model_feature", "GPU Model"),
    ("ram_size_feature", "RAM Volume"),
    ("gpu_type_feature", "GPU Type"),
    ("region_feature", "Region"),
]


def _spec_tags_from_features(ad: dict) -> list[str]:
    tags = []
    for feat_name, label in _SPEC_FEATURES:
        feat = ad.get(feat_name)
        if isinstance(feat, dict) and feat.get("value"):
            val = feat["value"]
            if isinstance(val, dict) and val.get("translated"):
                tags.append(f"[{label}: {val['translated']}]")
    return tags


def _price_mdl_from_ad(ad: dict) -> float:
    """Parse the price feature and convert EUR/USD amounts to MDL."""
    price_feature = ad.get("price")
    if not (isinstance(price_feature, dict) and price_feature.get("value")):
        return 0.0
    val_str = str(price_feature["value"]).lower()
    digits = re.findall(r"\d+", val_str.replace("\xa0", "").replace(" ", ""))
    if not digits:
        return 0.0
    raw_price = float("".join(digits))
    if "€" in val_str or "eur" in val_str:
        return raw_price * eur_to_mdl()
    if "$" in val_str or "usd" in val_str:
        return raw_price * usd_to_mdl()
    return raw_price


def _image_url_from_ad(ad: dict) -> str:
    """Build the first image URL from the GraphQL images feature (id 14)."""
    images_feature = ad.get("images")
    if isinstance(images_feature, dict) and images_feature.get("value"):
        try:
            img_vals = json.loads(images_feature["value"])
            if isinstance(img_vals, list) and img_vals:
                return f"https://i.999.md/m/{img_vals[0]}.jpg"
        except Exception:
            pass
    return ""


def parse_graphql_ad(ad: dict) -> dict:
    """Turn a raw GraphQL ad node into a normalized dict (no network/DB)."""
    ad_id = int(ad["id"])
    body = _description_from_ad(ad)
    tags = _spec_tags_from_features(ad)
    if tags:
        body = f"{body} {' '.join(tags)}"
    return {
        "id": ad_id,
        "title": ad.get("title", "").strip(),
        "url": f"https://999.md/ru/{ad_id}",
        "price": _price_mdl_from_ad(ad),
        "body": body,
        "image_url": _image_url_from_ad(ad),
    }


# ================= 2. HTML FETCHER (Legacy/Fallback) =================
def get_ad_html(url: str, retries: int = 3) -> str:
    """Fetch raw HTML of an ad page. Used when GraphQL body is empty."""
    for attempt in range(retries):
        try:
            delay = random.uniform(0.8, 1.8) if attempt == 0 else random.uniform(3.0, 6.0)
            time.sleep(delay)
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.text
            elif r.status_code in [403, 429]:
                log.warning("Server returned %d, waiting 10s...", r.status_code)
                time.sleep(10)
        except requests.exceptions.RequestException:
            if attempt < retries - 1:
                log.warning("Connection reset. Retry (%d/%d)...", attempt + 2, retries)
            else:
                log.error("Failed to fetch %s", url)
    return ""


def _html_fallback_description(ad_url: str) -> str:
    """Scrape the ad page for a description when GraphQL returns an empty body."""
    raw_html = get_ad_html(ad_url)
    if not raw_html:
        return ""
    try:
        soup = BeautifulSoup(raw_html, "html.parser")
        desc_div = soup.find(itemprop="description")
        if desc_div:
            body_content = desc_div.get_text(" ", strip=True)
        else:
            # Common 999.md description containers, newest class name first.
            container = (
                soup.find("div", class_="styles_description__body__qh1qw")
                or soup.find("div", class_="ad-description")
                or soup.find("div", class_="description-text")
                or soup.find("div", class_="description-body")
            )
            if container:
                body_content = container.get_text(" ", strip=True)
            else:
                for unwanted_tag in soup(["script", "style", "header", "nav", "footer", "aside"]):
                    unwanted_tag.extract()
                main_content = (
                    soup.find("main")
                    or soup.find("article")
                    or soup.find("div", class_=re.compile(r"content|description|body", re.IGNORECASE))
                )
                if main_content:
                    body_content = main_content.get_text(" ", strip=True)
                else:
                    body_content = soup.body.get_text(" ", strip=True) if soup.body else ""

        spec_tags = []
        for key, label in [
            ("Объем жесткого диска", "SSD"),
            ("Тип жесткого диска", "HDD Type"),
            ("Диагональ дисплея", "Screen"),
            ("Модель процессора", "CPU Model"),
            ("Модель видеокарты", "GPU Model"),
            ("Объем RAM", "RAM Volume"),
            ("Тип видеокарты", "GPU Type"),
        ]:
            for li in soup.find_all("li"):
                if key in li.get_text():
                    link = li.find("a")
                    if link:
                        spec_tags.append(f"[{label}: {link.get_text(strip=True)}]")
                        break
        div_map = soup.find("div", class_=re.compile(r"styles_map__address", re.IGNORECASE))
        if div_map:
            spec_tags.append(f"[Region: {div_map.get_text(strip=True)}]")
        if spec_tags:
            body_content = f"{body_content} {' '.join(spec_tags)}"
        return body_content
    except Exception as e:
        log.warning(f"Error parsing HTML for {ad_url}: {e}")
        return raw_html


# ================= 3. MAIN FETCH LOOP =================
def _search_variables(region: str, skip: int, limit: int) -> dict:
    return {
        "isWorkCategory": False,
        "includeCarsFeatures": False,
        "includeBody": True,  # Fetch the body directly via GraphQL
        "includeOwner": False,
        "includeBoost": False,
        "locale": "ru_RU",
        "input": {
            "source": "AD_SOURCE_DESKTOP_REDESIGN",
            "filters": [
                {"filterId": 290, "features": [{"featureId": 7, "optionIds": [12912]}]}
            ] if region == "balti" else [],
            "pagination": {"limit": limit, "skip": skip},
            "subCategoryId": 4,
        },
    }


def fetch_page(region: str, skip: int, limit: int) -> tuple[list[dict], int]:
    """One SearchAds page plus the category total. Raises on any failure."""
    resp = requests.post(
        GRAPHQL_URL,
        json={
            "operationName": "SearchAds",
            "query": FULL_QUERY,
            "variables": _search_variables(region, skip, limit),
        },
        headers=HEADERS,
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    search = (data.get("data") or {}).get("searchAds") or {}
    return search.get("ads") or [], int(search.get("count") or 0)


def fetch_all_ads(
    region: str,
    page_size: int = SCRAPE_PAGE_SIZE,
    max_ads: int = SCRAPE_MAX_ADS,
    fetch=fetch_page,
) -> list[dict]:
    """Page through the category until it runs out or *max_ads* is reached.

    A single 500-ad request used to be the whole scrape: every listing past the
    first page dropped out of the ranking and out of price tracking. A failure
    on the first page raises; a failure later keeps what was already fetched.
    """
    ads: list[dict] = []
    seen: set[str] = set()
    skip = 0
    while len(ads) < max_ads:
        limit = min(page_size, max_ads - len(ads))
        try:
            page, total = call_with_retry(
                lambda s=skip, n=limit: fetch(region, s, n),
                max_retries=3,
                base_delay_sec=5.0,
                label=f"999.md page skip={skip}",
            )
        except Exception:
            if not ads:
                raise
            log.warning("Page at skip=%d failed; keeping the %d ads fetched so far", skip, len(ads))
            break
        # New ads shift the listing while it is paged, so a page can repeat
        # entries from the previous one.
        fresh = [ad for ad in page if str(ad.get("id")) not in seen]
        seen.update(str(ad.get("id")) for ad in fresh)
        ads.extend(fresh)
        skip += len(page)
        log.info("Fetched %d ads (%d of %s in the category)", len(ads), skip, total or "?")
        if len(page) < limit or not fresh or (total and skip >= total):
            break
        time.sleep(SCRAPE_PAGE_DELAY_SEC)
    return ads[:max_ads]


def _store_ads(ads: list[dict]) -> dict[str, int]:
    """Upsert parsed ads and record price changes; returns the tally."""
    stats = {"new": 0, "price_drop": 0, "price_rise": 0, "unchanged": 0}
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        for ad in ads:
            parsed = parse_graphql_ad(ad)
            ad_id, title, ad_url = parsed["id"], parsed["title"], parsed["url"]
            price, body_content = parsed["price"], parsed["body"]

            old_price = get_current_price(cursor, ad_id)

            # If no description in GraphQL (rare), try to fetch it via HTML
            if not body_content and (old_price is None or abs(price - old_price) / max(old_price, 1) > 0.01):
                body_content = _html_fallback_description(ad_url) or body_content

            ad_data = {
                'ID': ad_id, 'Заголовок': title, 'Цена': price,
                'Валюта': 'MDL', 'Ссылка': ad_url, 'HTML_страницы': body_content,
                'Изображение': parsed["image_url"],
            }

            result = save_or_update_ad(cursor, ad_data)
            stats[result] = stats.get(result, 0) + 1

            if result == "new":
                log.info("✅ New:        %-45s | %6d MDL", title[:45], int(price))
            elif result == "price_drop":
                log.info("📉 Drop:       %-45s | %6d → %6d MDL  (-%d)", title[:45], int(old_price), int(price), int(old_price - price))
            elif result == "price_rise":
                log.info("📈 Rise:       %-45s | %6d → %6d MDL  (+%d)", title[:45], int(old_price), int(price), int(price - old_price))

        conn.commit()
    return stats


def fetch_and_process(region: str = "balti") -> dict[str, int] | None:
    """Fetch laptop ads from 999.md and upsert them into the local database.

    Returns the tally, or None when nothing could be fetched — the caller
    decides whether that fails the run.
    """
    log.info("Checking for new/updated ads...")

    if not FULL_QUERY:
        log.error("GraphQL query not loaded. Skipping fetch.")
        return None

    try:
        ads = fetch_all_ads(region)
        if not ads:
            log.error("No ads found in response.")
            return None
        stats = _store_ads(ads)
    except Exception as e:
        log.error("Error in fetch_and_process: %s", e, exc_info=True)
        return None

    log.info(
        "📊 Summary: new=%d | drops=%d | rises=%d | unchanged=%d",
        stats['new'], stats['price_drop'], stats['price_rise'], stats['unchanged'],
    )
    run_summary.write("Scrape", {
        "ads fetched": len(ads),
        "new": stats["new"],
        "price drops": stats["price_drop"],
        "price rises": stats["price_rise"],
    })

    # Show top-5 biggest price drops (≥5%) across entire database
    drops = get_price_drops(min_drop_pct=5.0)
    if drops:
        log.info("🔥 Top price drops (≥5%% from first seen):")
        for d in drops[:5]:
            log.info(f"   -{d['drop_pct']:.0f}%  {d['title'][:40]:<40} "
                     f"{int(d['first_price'])} → {int(d['last_price'])} MDL  {d['url']}")
    return stats


# ================= 4. CLI ENTRY POINT =================
def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch laptop ads from 999.md")
    parser.add_argument("--once", action="store_true", help="Run one fetch cycle and exit")
    parser.add_argument("--interval", type=int, default=CHECK_INTERVAL, help="Polling interval in seconds")
    parser.add_argument("--region", type=str, choices=["balti", "all"], default="balti", help="Region to search (balti or all)")
    args = parser.parse_args()

    init_database(DB_NAME)
    log.info("999.md parser started")
    log.info(f"Database: {DB_NAME} | Interval: {args.interval // 60} min | Region: {args.region}")

    if args.once:
        # A failed scrape must fail the run: otherwise the analyzer and the
        # digest carry on over yesterday's data as if it were today's.
        return 0 if fetch_and_process(region=args.region) is not None else 1

    while True:
        fetch_and_process(region=args.region)
        log.info(f"Next check in {args.interval // 60} min...")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
