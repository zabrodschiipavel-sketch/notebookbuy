"""Tests for Passmark fuzzy-match lookup (no network: items are injected)."""
from benchmarks import HardwareBenchmarker


def _bench(items: list[dict]) -> HardwareBenchmarker:
    """Build a benchmarker without triggering cache/network loading."""
    b = HardwareBenchmarker.__new__(HardwareBenchmarker)
    b.hw_type = "cpu"
    b.score_key = "cpumark"
    b.items = items
    b.names = [i["name"] for i in items]
    return b


ITEMS = [
    {"name": "Intel Core i7-12700H", "cpumark": 26000},
    {"name": "Intel Core i5-1235U", "cpumark": 13000},
    {"name": "AMD Ryzen 7 5800H", "cpumark": 21000},
]


def test_subset_match_returns_score():
    b = _bench(ITEMS)
    # Hyphenated model as produced by the regex parser.
    assert b.search("i7-12700h") == 26000
    # Multi-word query whose tokens are a subset of the DB name.
    assert b.search("ryzen 7 5800h") == 21000


def test_unknown_query_returns_zero():
    b = _bench(ITEMS)
    assert b.search("totally unknown chip xyz") == 0


def test_empty_query_returns_zero():
    b = _bench(ITEMS)
    assert b.search("") == 0


def test_no_items_returns_zero():
    b = _bench([])
    assert b.search("i7 12700h") == 0


GPU_ITEMS = [
    {"name": "GeForce 205", "g3d": 126},
    {"name": "GeForce RTX 3050 Laptop GPU", "g3d": 9500},
    {"name": "GeForce RTX 2060 (Mobile)", "g3d": 11353},
    {"name": "GeForce RTX 4060 Laptop GPU", "g3d": 17000},
    {"name": "NVIDIA A10", "g3d": 15000},
]


def _gpu_bench(items):
    b = _bench(items)
    b.hw_type, b.score_key = "gpu", "g3d"
    return b


def test_vendor_prefix_does_not_sink_the_match():
    """AI extraction writes the vendor, Passmark GPU names mostly omit it.
    Without the retry these matched 'GeForce 205' and an RTX 2060."""
    b = _gpu_bench(GPU_ITEMS)
    assert b.search("NVIDIA GeForce RTX 3050") == 9500
    assert b.search("NVIDIA RTX 4060") == 17000


def test_vendor_retry_needs_a_model_number():
    """'NVIDIA GeForce' alone names no card; it must not grab one at random."""
    b = _gpu_bench(GPU_ITEMS)
    assert b.search("NVIDIA GeForce Graphics") != 9500


def test_vendor_word_kept_when_the_name_has_it():
    b = _gpu_bench(GPU_ITEMS)
    assert b.search("NVIDIA A10") == 15000
