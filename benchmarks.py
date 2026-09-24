"""Passmark CPU / GPU benchmark data: fetch, cache, and fuzzy-match lookup."""
import json
import logging
import os
import re
import time

import requests
from rapidfuzz import fuzz, process

from app_config import PASSMARK_CACHE_DAYS


log = logging.getLogger(__name__)

PASSMARK_URLS = {
    "cpu": "https://www.cpubenchmark.net/CPU_mega_page.html",
    "gpu": "https://www.videocardbenchmark.net/GPU_mega_page.html",
}

PASSMARK_CACHE_FILES = {
    "cpu": "passmark_cpu.json",
    "gpu": "passmark_gpu.json",
}

# Words a query may carry that the Passmark name for the same part lacks.
_VENDOR_NOISE = frozenset({"nvidia", "amd", "intel", "graphics", "laptop", "gpu", "mobile"})
_VENDOR_NOISE_RE = re.compile(r"\b(?:" + "|".join(sorted(_VENDOR_NOISE)) + r")\b", re.IGNORECASE)


def _words(text: str) -> set[str]:
    return set(re.sub(r'[^a-z0-9\s]', '', text.lower()).split())


class HardwareBenchmarker:
    def __init__(self, hw_type: str):
        self.hw_type = hw_type
        self.score_key = "cpumark" if hw_type == "cpu" else "g3d"
        self.cache_file = PASSMARK_CACHE_FILES[hw_type]
        self.items = self._load_data()
        self.names = [item.get("name", "") for item in self.items]

    def _load_data(self) -> list[dict]:
        if self._is_cache_fresh():
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"Failed to load {self.hw_type} cache: {e}")

        return self._fetch_from_web()

    def _is_cache_fresh(self) -> bool:
        if not os.path.exists(self.cache_file):
            return False
        return (time.time() - os.path.getmtime(self.cache_file)) / 86400 < PASSMARK_CACHE_DAYS

    def _fetch_from_web(self) -> list[dict]:
        domain = "www.cpubenchmark.net" if self.hw_type == "cpu" else "www.videocardbenchmark.net"
        mega_url = PASSMARK_URLS[self.hw_type]
        data_url = f"https://{domain}/data/?_={int(time.time() * 1000)}"

        log.info(f"Fetching fresh {self.hw_type} benchmarks from Passmark...")
        headers = {"User-Agent": "Mozilla/5.0", "Referer": mega_url}

        try:
            session = requests.Session()
            session.get(mega_url, headers=headers, timeout=15).raise_for_status()
            resp = session.get(data_url, headers=headers, timeout=15)
            resp.raise_for_status()

            try:
                payload = resp.json()
            except Exception as e:
                raise RuntimeError(f"Passmark returned non-JSON for {self.hw_type}") from e

            raw = payload.get("data", []) if isinstance(payload, dict) else []
            if not isinstance(raw, list):
                raw = []

            items = []
            for entry in raw:
                name = str(entry.get("name", "")).strip()
                score = entry.get(self.score_key) or entry.get("cpumark") or entry.get("g3d") or 0
                try:
                    score_int = int(str(score).replace(",", ""))
                except (ValueError, TypeError) as e:
                    log.debug(f"Bad {self.score_key} score for '{name}': {score!r} ({e})")
                    score_int = 0
                if name and score_int > 0:
                    items.append({"name": name, self.score_key: score_int})

            if items:
                with open(self.cache_file, "w", encoding="utf-8") as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
            return items
        except Exception as e:
            log.error(f"Error fetching {self.hw_type} data: {e}")
            return []

    def search(self, query: str) -> int:
        """Return the benchmark score for *query*, or 0 if not found."""
        if not query or not self.items:
            return 0

        # 1. Subset match (fast)
        cleaned = self._clean_name(query)
        q_words = _words(cleaned)
        if not q_words:
            return 0

        candidates = self._subset_candidates(q_words)
        if not candidates:
            # The AI writes "NVIDIA GeForce RTX 3050"; Passmark lists "GeForce
            # RTX 3050 ...". One vendor word the name lacks sank the subset
            # match, and the fuzzy pass then settled on "GeForce 205" — 126
            # points for a ~10 000-point card. Retry without such words, but
            # only while a model number remains to anchor the match.
            core = q_words - _VENDOR_NOISE
            if core != q_words and any(ch.isdigit() for word in core for ch in word):
                candidates = self._subset_candidates(core)
                cleaned = " ".join(_VENDOR_NOISE_RE.sub(" ", cleaned).split())

        if candidates:
            # Rank candidates by name similarity to query, and return the closest match's score
            best = max(candidates, key=lambda c: fuzz.WRatio(cleaned, c.get("name", "")))
            return best.get(self.score_key, 0)

        # 2. Fuzzy match (slower)
        match = process.extractOne(cleaned, self.names, scorer=fuzz.WRatio)
        if match and match[1] >= 78:
            return self.items[match[2]].get(self.score_key, 0)

        return 0

    def _subset_candidates(self, q_words: set[str]) -> list[dict]:
        return [item for item in self.items if q_words <= _words(item.get("name", ""))]

    @staticmethod
    def _clean_name(name: str) -> str:
        if not name:
            return ""
        return str(name).split('(')[0].split('/')[0].strip()
