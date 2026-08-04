import datetime
import hashlib
import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from bs4 import BeautifulSoup

from ai_service import AIService
from app_config import (
    ADS_ANALYZE_LIMIT,
    DB_NAME,
    ENABLE_EXTERNAL_LOOKUPS,
    EXTERNAL_MISS_TTL_DAYS,
    GEMINI_API_KEY,
    GEMINI_MAX_WORKERS,
    GEMINI_SEARCH_DELAY_SEC,
    MIN_CPU_SCORE,
    WORLD_PRICE_TOP_N,
)
from benchmarks import HardwareBenchmarker
from db import init_database
from estimation import (
    estimate_fallback_price,
    estimate_fallback_score,
    external_cache_key,
    plausible_nbc_score,
)
from parser import LaptopParser
from scoring import (
    ANALYSIS_VERSION,
    CATEGORY_EMOJI,
    CATEGORY_LABEL,
    MAX_PRICE_MDL,
    MDL_USD_RATE,  # Import MDL_USD_RATE
    MIN_PRICE_MDL,
    MIN_YEAR,
    is_unwanted_ad,
    score_laptop,
)


# ================= 1. CONFIGURATION =================
WORLD_PRICE_CACHE = "pricehistory_cache.json"
NBC_CACHE_FILE = "notebookcheck_cache.json"

# Marker stored for lookups that returned nothing, so a dead question is not
# re-asked on every run. Positive hits written before this existed have no
# marker and stay valid forever, as they did before.
_MISS_MARKER = "__miss_at__"


def _miss_entry() -> dict:
    return {_MISS_MARKER: datetime.date.today().isoformat()}


def _is_miss(entry: object) -> bool:
    return isinstance(entry, dict) and _MISS_MARKER in entry


def _is_fresh_cache_entry(entry: object) -> bool:
    """True when the cached answer can be reused instead of re-querying."""
    if not entry:
        return False
    if not _is_miss(entry):
        return True
    try:
        recorded = datetime.date.fromisoformat(entry[_MISS_MARKER])
    except (TypeError, ValueError):
        return False
    return (datetime.date.today() - recorded).days < EXTERNAL_MISS_TTL_DAYS

if not GEMINI_API_KEY:
    logging.warning("GEMINI_API_KEY is not set — AI extraction and external lookups will be skipped")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# Fallback estimation now lives in estimation.py (shared with send_telegram.py).


# ================= 3. DATABASE MANAGER =================
class DatabaseManager:
    """Thin wrapper around SQLite for analysis cache read/write operations."""

    def __init__(self, db_name: str):
        self.db_name = db_name
        init_database(db_name)

    def get_cache(self) -> dict[str, Any]:
        with sqlite3.connect(self.db_name) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM analysis_cache")
            return {str(row["id"]): dict(row) for row in cursor.fetchall()}

    def save_analysis(self, data: dict[str, Any]):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO analysis_cache (
                    id, cpu, gpu, ram, ssd, is_broken, year_est, cpu_score, gpu_score,
                    content_hash, analysis_version, analyzed_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    data['id'], data['cpu'], data['gpu'], data['ram'], data['ssd'],
                    int(data['is_broken']), data['year_est'], data['cpu_score'], data['gpu_score'],
                    data.get("content_hash"), ANALYSIS_VERSION, datetime.datetime.now()
                ),
            )
            conn.commit()


# ================= 4. ANALYZER CORE =================
class LaptopAnalyzer:
    """Main pipeline: parse ads → extract specs → benchmark lookup → score → report."""

    def __init__(self):
        self.db = DatabaseManager(DB_NAME)
        self.cpu_bench = HardwareBenchmarker("cpu")
        self.gpu_bench = HardwareBenchmarker("gpu")
        self.ai = AIService()
        self.parser = LaptopParser()

    def _get_external_data(self, processed_laptops: list[dict]) -> tuple[dict, dict]:
        # Initialize prices and ratings with empty dicts
        prices = {}
        ratings = {}

        # If external lookups are disabled or AI client is not available,
        # we still want to apply fallback logic.
        if ENABLE_EXTERNAL_LOOKUPS and self.ai.client:
            price_cache = self._load_json_cache(WORLD_PRICE_CACHE)
            nbc_cache = self._load_json_cache(NBC_CACHE_FILE)

            dirty = False

            # Use ThreadPoolExecutor for parallel search
            def process_laptop_external(lap: dict):
                nonlocal dirty
                # Cache key: CPU + GPU + RAM (shared with dashboard/notifier)
                key = external_cache_key(lap['cpu'], lap['gpu'], lap['ram'])

                def lookup(cache: dict, label: str, prompt: str) -> dict:
                    nonlocal dirty
                    cached = cache.get(key)
                    if _is_fresh_cache_entry(cached):
                        return {} if _is_miss(cached) else cached

                    log.info(f"Searching {label}: {lap['title'][:30]}")
                    data = self.ai.google_search_json(prompt)
                    # Remember failures too, otherwise the next run re-asks the
                    # same dead question and spends the quota again.
                    cache[key] = data if data else _miss_entry()
                    dirty = True
                    time.sleep(GEMINI_SEARCH_DELAY_SEC)  # Minimal delay between searches
                    return data

                p_data = lookup(
                    price_cache,
                    "World Price",
                    f"Search launch price and current global price (USD) for laptop: "
                    f"{lap['title']} CPU: {lap['cpu']} GPU: {lap['gpu']}. "
                    f"Return JSON: {{\"launch_usd\": N, \"current_usd\": N}}",
                )
                r_data = lookup(
                    nbc_cache,
                    "Review",
                    f"Find rating on Notebookcheck.net for: {lap['title']} "
                    f"CPU: {lap['cpu']} GPU: {lap['gpu']}. "
                    f"Return JSON: {{\"score\": int_percentage, \"url\": \"url\"}}",
                )

                return lap['id'], p_data, r_data

            with ThreadPoolExecutor(max_workers=min(3, GEMINI_MAX_WORKERS)) as executor:
                futures = [executor.submit(process_laptop_external, lap) for lap in processed_laptops[:WORLD_PRICE_TOP_N]]
                for future in as_completed(futures):
                    l_id, p_val, r_val = future.result()
                    if p_val:
                        prices[l_id] = p_val
                    if r_val:
                        ratings[l_id] = r_val

            if dirty:
                self._save_json_cache(WORLD_PRICE_CACHE, price_cache)
                self._save_json_cache(NBC_CACHE_FILE, nbc_cache)

        # Apply fallback logic for any missing external data
        for lap in processed_laptops:
            lap_id = lap['id']

            # Ensure cpu, gpu, ram, ssd are available for fallback functions
            cpu_val = lap.get('cpu', '')
            gpu_val = lap.get('gpu', '')
            ram_val = lap.get('ram', 0)
            ssd_val = lap.get('ssd', 0)

            # Fallback for World Price
            if lap_id not in prices or not prices[lap_id] or not prices[lap_id].get('current_usd'):
                estimated_price_mdl = estimate_fallback_price(
                    cpu_val, gpu_val, ram_val, ssd_val
                )
                prices[lap_id] = {"current_usd": estimated_price_mdl / MDL_USD_RATE, "fallback": True}

            # Fallback for NBC Score (also when the AI search returned an
            # implausible rating — e.g. the 12%/15% hallucinations).
            if lap_id not in ratings or not plausible_nbc_score((ratings[lap_id] or {}).get('score')):
                estimated_score = estimate_fallback_score(
                    cpu_val, gpu_val, ram_val
                )
                ratings[lap_id] = {"score": estimated_score, "fallback": True}

        return prices, ratings

    @staticmethod
    def _load_json_cache(path: str) -> dict:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"Failed to read cache {path}: {e}")
        return {}

    @staticmethod
    def _save_json_cache(path: str, data: dict):
        with open(path, 'w', encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def run(self):  # noqa: C901
        log.info("Starting analysis v3...")
        init_time = datetime.datetime.now().replace(microsecond=0) # Round to second for report name

        with sqlite3.connect(DB_NAME) as conn:
            ads = conn.execute(
                "SELECT id, title, price, description, url FROM ads "
                "WHERE parsed_at >= datetime(substr((SELECT MAX(parsed_at) FROM ads), 1, 19), '-2 hours') "
                "ORDER BY id DESC LIMIT ?",
                (ADS_ANALYZE_LIMIT,),
            ).fetchall()
            cached_data = self.db.get_cache()

            final_list, to_ai_batch = [], []

            for row in ads:
                ad_id, title, price, desc, url = str(row[0]), row[1], row[2], row[3], row[4]
                if not (MIN_PRICE_MDL <= price <= MAX_PRICE_MDL):
                    continue

                # Skip buying requests ("куплю/cumpăr") and shop spam
                if is_unwanted_ad(title, desc):
                    continue

                # desc might contain body text from GraphQL
                text = BeautifulSoup(desc or "", "html.parser").get_text(" ")
                content_hash = hashlib.sha256(f"{title}\n{text}".encode("utf-8", "ignore")).hexdigest()
                cached = cached_data.get(ad_id)
                cache_is_current = (
                    cached
                    and cached.get("content_hash") == content_hash
                    and cached.get("analysis_version") == ANALYSIS_VERSION
                )

                if cache_is_current:
                    specs = {
                        "id": ad_id,
                        "cpu": cached["cpu"],
                        "gpu": cached["gpu"],
                        "ram": cached["ram"],
                        "ssd": cached["ssd"],
                        "is_broken": bool(cached["is_broken"]),
                        "year_est": cached["year_est"],
                        "cpu_score": cached["cpu_score"],
                        "gpu_score": cached["gpu_score"],
                        "content_hash": content_hash,
                    }
                else:
                    specs = self.parser.regex_parse(text, title)
                    specs['id'] = ad_id
                    specs["content_hash"] = content_hash

                    if not specs['cpu'] or specs['ram'] == 0:
                        to_ai_batch.append({
                            "id": ad_id,
                            "text": text,
                            "title": title,
                            "content_hash": content_hash,
                        })
                        continue

                    specs['cpu_score'] = self.cpu_bench.search(specs['cpu'])
                    specs['gpu_score'] = self.gpu_bench.search(specs['gpu']) if specs['gpu'] != "integrated" else 0
                    self.db.save_analysis(specs)

                specs.update({"title": title, "price": price, "url": url})
                final_list.append(specs)

            if to_ai_batch:
                log.info(f"AI Batch Processing: {len(to_ai_batch)} ads...")
                batch_map = {a['id']: a for a in to_ai_batch}
                ads_map = {str(row[0]): row for row in ads}
                ai_results = self.ai.extract_specs(to_ai_batch)
                for res in ai_results:
                    orig = batch_map.get(res['id'])
                    ad_ref = ads_map.get(res['id'])
                    if not orig or not ad_ref:
                        log.warning(f"AI returned unknown id {res['id']}, skipping")
                        continue
                    res['year_est'] = self.parser.estimate_year(res['cpu'])
                    res['cpu_score'] = self.cpu_bench.search(res['cpu'])
                    res['gpu_score'] = self.gpu_bench.search(res['gpu']) if res['gpu'] != "integrated" else 0
                    res["content_hash"] = orig["content_hash"]
                    self.db.save_analysis(res)
                    res.update({"title": orig['title'], "price": ad_ref[2], "url": ad_ref[4]})
                    final_list.append(res)

        # Advanced Scoring & Classification
        processed = []
        price_by_cat = defaultdict(list)
        current_year = datetime.datetime.now().year

        for lap in final_list:
            cpu_val = lap.get('cpu_score') or 0
            if cpu_val < MIN_CPU_SCORE:
                continue

            year = lap.get('year_est') or current_year - 5
            if year < MIN_YEAR:
                continue

            ram = lap.get('ram') or 4
            ssd_gb = lap.get('ssd') or 0
            gpu_val = lap.get('gpu_score') or 0
            category = self.parser.classify(lap['cpu'], gpu_val, lap['price'])

            scored = score_laptop(
                cpu_score=cpu_val, gpu_score=gpu_val, ram=ram, ssd=ssd_gb,
                year_est=year, price=lap['price'], is_broken=lap.get("is_broken", False)
            )
            tech_pts = scored["tech_pts"]

            lap.update({
                "value": scored["value_score"],
                "pts": tech_pts,
                "category": category,
                "cpu_disp": self.parser.normalize_cpu(lap['cpu']),
            })
            processed.append(lap)
            price_by_cat[category].append(lap['price'])

        processed.sort(key=lambda x: x['value'], reverse=True)

        # Medians
        medians = {}
        for cat, prices in price_by_cat.items():
            s = sorted(prices)
            medians[cat] = s[len(s)//2]

        # External Data (now includes fallback logic)
        world_prices, nbc_ratings = self._get_external_data(processed)

        # Reporting
        report_name = f"top_laptops_{init_time.strftime('%Y%m%d_%H%M')}.txt"
        with open(report_name, "w", encoding="utf-8") as f:
            def w(s=""): f.write(s + "\n")

            w("╔══════════════════════════════════════════════════════════════════════════════╗")
            w(f"║  🏆  ТОП НОУТБУКОВ 999.MD — {init_time.strftime('%d.%m.%Y %H:%M'):<47}║")
            w("╚══════════════════════════════════════════════════════════════════════════════╝")
            w()
            w(
                f"  Всего в рейтинге: {len(processed)} | Медианы: "
                + ", ".join([f"{k}: {int(v)}" for k, v in medians.items()])
            )
            w()

            by_cat = defaultdict(list)
            for rank, r in enumerate(processed[:40], start=1):
                by_cat[r['category']].append((rank, r))

            for cat in ["MacBook", "Gaming", "Ultrabook"]:
                items = by_cat.get(cat, [])
                if not items:
                    continue

                label = CATEGORY_LABEL.get(cat, cat.upper())
                w(f"  ┌─ {CATEGORY_EMOJI.get(cat, '')} {label} {'─' * (68 - len(label))}┐")
                w(f"  │ {'#':<3} {'Цена':>6}  {'±рынок':>7}  {'Год':>4}  {'RAM':>3}GB  {'GPU':>6}pts  {'CPU':<25} │")
                w(f"  ├{'─'*74}┤")

                med = medians.get(cat, 1)
                for rank, r in items:
                    diff = ((r['price'] - med) / med * 100)
                    diff_str = f"{diff:+.0f}%"
                    w(f"  │ {rank:<3} {r['price']:>6} MDL  {diff_str:>6}   {r['year_est'] or '?'}  {r['ram']:>3}GB  {r['gpu_score']:>6}     {r['cpu_disp']:<25} │")
                    w(f"  │     {r['title'][:68]:<68} │")
                    w(f"  │     {r['url'][:68]:<68} │")
                    w(f"  │{'─'*74}│")
                w(f"  └{'─'*74}┘\n")

            # World Price Table
            advanced = [r for r in processed[:WORLD_PRICE_TOP_N]]
            if advanced:
                W = 110
                w("  ┌─ 🌍 МИРОВОЙ РЫНОК + ОБЗОРЫ " + "─" * (W-32) + "┐")
                w(f"  │ {'#':<3} {'Цена':>9}  {'Запуск':>9}  {'Текущая':>9}  {'vs Тек.':>9} │ {'NBC %':>6} │ {'Ссылка':<35} │")
                w(f"  ├{'─'*W}┤")

                for rank, r in enumerate(advanced, start=1):
                    wp = world_prices.get(r['id'], {})
                    nbc = nbc_ratings.get(r['id'], {})

                    l_usd = f"${wp.get('launch_usd')}" if wp.get('launch_usd') else "—"
                    c_usd = f"${wp.get('current_usd')}" if wp.get('current_usd') else "—"

                    vs_pct = "—"
                    # Use the current_usd from wp, which now includes fallbacks
                    if wp.get('current_usd'):
                        world_mdl = wp['current_usd'] * MDL_USD_RATE
                        # Avoid division by zero if world_mdl is 0
                        if world_mdl != 0:
                            diff = (r['price'] - world_mdl) / world_mdl * 100
                            icon = "🟢" if diff < -10 else ("🟡" if diff < 10 else "🔴")
                            vs_pct = f"{icon}{diff:+.0f}%"
                        else:
                            vs_pct = "N/A" # Or some other indicator for invalid world_mdl
                        if wp.get('fallback'): # Indicate if it's a fallback value
                            vs_pct += "*"

                    score = f"{nbc.get('score')}%" if nbc.get('score') else "—"
                    if nbc.get('score') and nbc['score'] >= 85:
                        score = f"🔥{score}"
                    if nbc.get('fallback'): # Indicate if it's a fallback value
                        score += "*"

                    w(f"  │ {rank:<3} {r['price']:>7} MDL  {l_usd:>9}  {c_usd:>9}  {vs_pct:>9} │ {score:>6} │ {str(nbc.get('url'))[:35]:<35} │")
                w(f"  └{'─'*W}┘")
                w("\n* - Estimated value (fallback)")

        log.info(f"Report saved to {report_name}")


if __name__ == "__main__":

    analyzer = LaptopAnalyzer()
    analyzer.run()
