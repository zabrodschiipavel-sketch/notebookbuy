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
    r'i[3579][-\s]\d{4,5}[hxugt]{0,3}'            # Intel Core i7-12700H, i5-1135G7
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

RAM_REGEX = re.compile(r'\b(4|6|8|12|16|24|32|48|64|128)\s*(?:gb|гб|g)\b')
YEAR_REGEX = re.compile(r'\b(20(?:0[8-9]|1[0-9]|2[0-5]))\b') # Years from 2008 to 2025


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
        full_text = f"{title} {text}".lower()

        cpu_match = CPU_REGEX.search(full_text)
        gpu_match = GPU_REGEX.search(full_text)
        ram_match = RAM_REGEX.search(full_text)
        year_match = YEAR_REGEX.search(full_text)

        cpu = cpu_match.group(0).strip() if cpu_match else ""

        if re.search(r'm[1234]', cpu, re.IGNORECASE):
            if not re.search(r'(?:apple|macbook)', full_text):
                cpu = ""

        ssd_val = LaptopParser._extract_ssd(full_text)

        year_est = None
        if year_match:
            year_est = int(year_match.group(1))
        else:
            year_est = LaptopParser.estimate_year(cpu) # Fallback to CPU-based estimation

        is_apple = bool(re.search(r'm[1234]', cpu, re.IGNORECASE)) or any(
            w in full_text for w in ['apple', 'macbook']
        )
        ssd_val = infer_ssd_gb(ssd_val, year_est, is_apple)

        return {
            "cpu": cpu,
            "gpu": gpu_match.group(0).strip() if gpu_match else "integrated",
            "ram": int(ram_match.group(1)) if ram_match else 0,
            "ssd": ssd_val,
            # Only genuine defect/lock signals. Urgency words ("срочно", "urgent",
            # "без торга") are NOT defects and must not trigger the broken penalty.
            "is_broken": any(k in full_text for k in [
                "запчаст", "дефект", "не работ", "разбит экран", "треснут экран", "битый экран", "экран не работ",
                "не включ", "piese", "defect", "parola", "blocat", "заблокирован", "на запчасти",
            ]),
            "year_est": year_est
        }
