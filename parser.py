"""Regex-based hardware spec extraction from laptop ad text.

Precompiles patterns for CPU, GPU, RAM, and SSD at import time so
``LaptopParser.regex_parse`` runs fast even on large batches.
"""

import re
from typing import Any

from scoring import (
    classify_laptop,
    estimate_year_from_cpu,
    infer_ssd_gb,
    normalize_cpu_name,
)


# Precompiled regular expressions for performance
CPU_REGEX = re.compile(
    r'\b('
    r'i[3579][-\s]\d{4,5}(?:[a-z]{1,2}\d?)?'       # Intel Core i7-12700H, i9-13900HX, i5-1135G7
    r'|core\s+ultra\s+[3579]\s+\d{3,5}[hxug]?'     # Intel Core Ultra 7 155H
    r'|ryzen\s+[3579]\s+\d{4}[hxusg]{0,3}'         # AMD Ryzen 7 5800H
    r'|snapdragon\s*(?:x|8[a-z0-9]*)'              # Snapdragon X Elite, 8cx
    r'|m[1234]\s*(?:pro|max|ultra)?'               # Apple M1, M2 Pro, M3 Max (will be post-processed for brand)
    r'|celeron\s*(?:gold|silver)?\s*[a-z0-9]+'     # Intel Celeron N4020
    r'|pentium\s*(?:gold|silver)?\s*[a-z0-9]+'     # Intel Pentium Gold 7505
    r'|xeon\s*[a-z0-9-]+'                          # Intel Xeon E3-1535M
    r'|athlon\s*(?:gold|silver)?\s*[a-z0-9]+'      # AMD Athlon Gold 3150U
    r'|i[3579](?!\d)'                              # Generic Intel Core i3/i5/i7/i9
    r')\b'
)

GPU_REGEX = re.compile(
    r'\b('
    r'rtx\s*\d{3,4}(?:\s*ti)?'                     # NVIDIA RTX 3060, 4070 Ti
    r'|gtx\s*\d{3,4}(?:\s*ti)?'                    # NVIDIA GTX 1650
    r'|rx\s*\d{3,4}(?:\s*xt)?'                     # AMD RX 6600 XT
    r'|radeon\s+(?:\d{3,4}m?|pro|graphics|graphics\s+\d+|780m|680m)' # AMD Radeon
    r'|geforce\s+mx\s*\d{3}'                       # NVIDIA MX350, MX450
    r'|iris\s+xe'                                  # Intel Iris Xe
    r'|intel\s+(?:uhd|hd)\s*(?:graphics)?\s*\d*'   # Intel UHD/HD Graphics
    r')\b'
)

RAM_SIZES = ('4', '6', '8', '12', '16', '24', '32', '48', '64', '128')
# "16gb ram", "озу 8 гб", "RAM16GB", "DDR5 32Gb" — a size explicitly tied to a
# RAM keyword. Guard rails, all hit by real ads:
#  - lookbehinds keep GPU VRAM ("GDDR6 8GB"), words ending in "ram"
#    ("program"), and digits glued to CPU models ("i5-1035g1 ram") out;
#  - the keyword-to-size gap allows only spaces/colons, so a config list like
#    "ddr3 / 128gb ssd" cannot tie the SSD size to the RAM keyword;
#  - bare "memorie" is NOT a keyword: "GTX 1660 Ti cu 6 gb memorie dedicată"
#    is Romanian for GPU VRAM.
RAM_KEYWORD_REGEX = re.compile(
    r'(?<![a-zа-яё0-9])(?:ram|озу|оперативн\w*|ddr[2-5])[\s:]{0,3}(\d{1,3})\s*(?:gb|гб|g\b)'
    r'|(?<![a-z0-9])(\d{1,3})\s*(?:gb|гб|g)?\s*(?:of\s+)?(?:ram|озу|оперативн\w*|(?<!g)ddr[2-5])\b'
)
RAM_REGEX = re.compile(r'\b(4|6|8|12|16|24|32|48|64|128)\s*(?:gb|гб|g)\b')
# Sizes that belong to storage, not RAM: "128gb ssd", "512 гб nvme".
_STORAGE_AFTER_RE = re.compile(r'^\s*(?:ssd|nvme|hdd|emmc|ссд|m\.2)')
# Sizes that belong to GPU VRAM: "RTX 3050Ti 4Gb", "видеокарта 6 гб".
_GPU_BEFORE_RE = re.compile(r'(?:rtx|gtx|radeon|geforce|vram|video|видео\w*).{0,12}$')
YEAR_REGEX = re.compile(r'\b(20(?:0[8-9]|1[0-9]|2[0-5]))\b') # Years from 2008 to 2025
# "гарантия до 2026" / "garantie pana in 2026" — not the release year.
_WARRANTY_BEFORE_RE = re.compile(r'(?:гарант\w*|garan\w*|warranty|до|pina|pana|until)[\s:]*$')


class LaptopParser:
    @staticmethod
    def estimate_year(cpu_name: str) -> int | None:
        return estimate_year_from_cpu(cpu_name)

    @staticmethod
    def normalize_cpu(name: str) -> str:
        return normalize_cpu_name(name)

    @staticmethod
    def classify(cpu: str, gpu_score: int, price: int) -> str:
        return classify_laptop(cpu, gpu_score, price)

    @staticmethod
    def _extract_ram(text: str) -> int:
        """RAM size in GB, robust to storage sizes appearing first.

        Priority: a size explicitly tied to a RAM keyword ("16gb ram",
        "RAM16GB", "озу 8гб"); otherwise the first plausible standalone size
        that is NOT immediately followed by a storage keyword — previously
        "128gb ssd, 8gb ram" parsed as 128 GB of RAM.
        """
        if not text:
            return 0

        for kw_match in RAM_KEYWORD_REGEX.finditer(text):
            val = int(kw_match.group(1) or kw_match.group(2))
            if str(val) in RAM_SIZES:
                return val

        for m in RAM_REGEX.finditer(text):
            if _STORAGE_AFTER_RE.search(text[m.end():m.end() + 8]):
                continue
            if _GPU_BEFORE_RE.search(text[max(0, m.start() - 20):m.start()]):
                continue
            return int(m.group(1))
        return 0

    @staticmethod
    def _extract_year(full_text: str) -> int | None:
        """First plausible year that is not part of a warranty phrase."""
        for m in YEAR_REGEX.finditer(full_text):
            if not _WARRANTY_BEFORE_RE.search(full_text[max(0, m.start() - 16):m.start()]):
                return int(m.group(1))
        return None

    @staticmethod
    def _extract_ssd(text: str) -> int:
        if not text:
            return 0
        text = str(text).lower()

        # Сначала ищем терабайты (1tb, 1 tb, 2тб)
        tb_match = re.search(r'\b(1|2)\s*(tb|тб|terabyte)\b', text)
        if tb_match:
            return int(tb_match.group(1)) * 1024

        # Затем ищем типичные объемы SSD в гигабайтах (256gb, 512, 1024 гб)
        gb_match = re.search(r'\b(128|250|256|500|512|1000|1024|2000|2048)\s*(gb|гб|g|ssd)?\b', text)
        if gb_match:
            return int(gb_match.group(1))

        return 0

    @staticmethod
    def regex_parse(text: str, title: str) -> dict[str, Any]:
        # Literal "\n" sequences (already-scraped ads stored a stringified
        # translations dict) glue letters to sizes: "hx\n16gb ram" hides the
        # RAM from every word-boundary regex. Treat them as whitespace.
        full_text = f"{title} {text}".lower().replace("\\n", " ")

        cpu_match = CPU_REGEX.search(full_text)
        gpu_match = GPU_REGEX.search(full_text)

        cpu = cpu_match.group(0).strip() if cpu_match else ""

        # An M-chip claim needs Apple context in the *title* (or an explicit
        # "apple m2" in the text). A body mention like "как macbook" or an
        # "ssd m2" used to give a Xiaomi an Apple CPU and its benchmark score.
        if re.search(r'm[1234]', cpu, re.IGNORECASE):
            title_l = title.lower()
            if not (
                re.search(r'\b(?:apple|macbook|mac|imac)\b', title_l)
                or re.search(r'apple\s*m[1234]', full_text)
            ):
                cpu = ""

        ssd_val = LaptopParser._extract_ssd(full_text)

        year_est = LaptopParser._extract_year(full_text)
        if year_est is None:
            year_est = LaptopParser.estimate_year(cpu) # Fallback to CPU-based estimation
        else:
            # Cross-check against the CPU generation: a text year far ahead of
            # the CPU's launch year is usually a warranty/«до 2026» artefact.
            cpu_year = LaptopParser.estimate_year(cpu) if cpu else None
            if cpu_year and year_est > cpu_year + 2:
                year_est = cpu_year

        is_apple = bool(re.search(r'm[1234]', cpu, re.IGNORECASE)) or any(
            w in full_text for w in ['apple', 'macbook']
        )
        ssd_val = infer_ssd_gb(ssd_val, year_est, is_apple)

        return {
            "cpu": cpu,
            "gpu": gpu_match.group(0).strip() if gpu_match else "integrated",
            "ram": LaptopParser._extract_ram(full_text),
            "ssd": ssd_val,
            # Only genuine defect/lock signals. Urgency words ("срочно", "urgent",
            # "без торга") are NOT defects and must not trigger the broken penalty.
            "is_broken": any(k in full_text for k in [
                "запчаст", "дефект", "не работ", "разбит экран", "треснут экран", "битый экран", "экран не работ",
                "не включ", "piese", "defect", "parola", "blocat", "заблокирован", "на запчасти",
            ]),
            "year_est": year_est
        }
