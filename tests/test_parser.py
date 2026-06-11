"""Tests for regex-based hardware spec extraction."""
import pytest

from parser import LaptopParser


@pytest.mark.parametrize(
    "title,text,expected_cpu,expected_gpu,expected_ram,expected_ssd,expected_broken",
    [
        (
            "Lenovo Legion 5",
            "Игровой ноутбук с RTX 3060, Ryzen 7 5800H, 16gb гб ram, 512gb ssd. В идеале.",
            "ryzen 7 5800h",
            "rtx 3060",
            16,
            512,
            False,
        ),
        (
            "Macbook Air 13 M1",
            "Продаю макбук м1 8гб озу 256 gb ssd nvme",
            "m1",
            "integrated",
            8,
            256,
            False,
        ),
        (
            "HP Pavilion",
            "Ноутбук Celeron N4020 4GB RAM 128GB SSD. Треснут экран, на запчасти",
            "celeron n4020",
            "integrated",
            4,
            128,
            True,
        ),
        (
            "Samsung 750XED - i5 1235u",
            "Шустрый i5 1235u, 8gb ram, 512 ssd",
            "i5 1235u",
            "integrated",
            8,
            512,
            False,
        ),
        (
            "Asus Vivobook",
            "Intel Pentium Gold 7505, Intel UHD Graphics, 16 gb ram, 1tb hdd",
            "pentium gold 7505",
            "intel uhd graphics",
            16,
            1024,
            False,
        ),
    ],
)
def test_regex_parse(
    title: str,
    text: str,
    expected_cpu: str,
    expected_gpu: str,
    expected_ram: int,
    expected_ssd: int,
    expected_broken: bool,
) -> None:
    res = LaptopParser.regex_parse(text, title)
    assert res["cpu"] == expected_cpu
    assert res["gpu"] == expected_gpu
    assert res["ram"] == expected_ram
    assert res["ssd"] == expected_ssd
    assert res["is_broken"] == expected_broken


# --- Edge cases ---

def test_empty_strings_return_defaults():
    res = LaptopParser.regex_parse("", "")
    assert res["cpu"] == ""
    assert res["gpu"] == "integrated"
    assert res["ram"] == 0
    assert res["ssd"] == 0
    assert res["is_broken"] is False


def test_gpu_only_no_cpu():
    res = LaptopParser.regex_parse("Видеокарта RTX 4060 ti, 16 gb", "Gaming laptop")
    assert res["gpu"] == "rtx 4060 ti"
    assert res["cpu"] == ""  # no CPU in text


def test_tb_ssd_units():
    res = LaptopParser.regex_parse("2tb ssd nvme", "Workstation")
    assert res["ssd"] == 2048


def test_one_gb_ssd_not_treated_as_terabyte():
    res = LaptopParser.regex_parse("1 gb ssd", "Budget laptop")
    assert res["ssd"] == 0


def test_n_series_cpu():
    res = LaptopParser.regex_parse("Intel Celeron N5030 4gb ram 64gb ssd", "HP Stream")
    assert "n5030" in res["cpu"]


def test_is_broken_false_positive_screen():
    """Mentioning 'экран' alone should NOT flag as broken."""
    res = LaptopParser.regex_parse("IPS экран 15.6 дюймов, Full HD", "Lenovo Ideapad")
    assert res["is_broken"] is False


def test_is_broken_true_for_spare_parts():
    res = LaptopParser.regex_parse("Ноутбук на запчасти, не включается", "Dell E5540")
    assert res["is_broken"] is True


@pytest.mark.parametrize(
    "text",
    [
        "Продам срочно, отличное состояние",
        "Urgent sale, like new",
        "Цена без торга, всё работает идеально",
    ],
)
def test_urgency_is_not_broken(text: str):
    """Urgency wording must not trigger the broken-device penalty."""
    res = LaptopParser.regex_parse(text, "Lenovo ThinkPad i7-1165G7 16gb 512gb")
    assert res["is_broken"] is False


def test_apple_m3_max():
    res = LaptopParser.regex_parse("Apple M3 Max 48gb ram 1tb ssd", "MacBook Pro 16")
    assert "m3 max" in res["cpu"]
    assert res["ram"] == 48
    assert res["ssd"] == 1024


# --- Regressions caught on real 999.md ads (May 2026 database) ---

def test_ram_not_stolen_by_ssd_size():
    """'128gb ssd' listed before RAM must not become 128 GB of RAM."""
    res = LaptopParser.regex_parse("128gb ssd, 8gb ram, i5-1135g7", "Ноутбук")
    assert res["ram"] == 8
    assert res["ssd"] == 128


def test_ram_not_stolen_by_gpu_vram():
    """Dell XPS pattern: GPU VRAM listed before RAM."""
    res = LaptopParser.regex_parse("", "Dell XPS 15 (i7 12700H/RTX 3050Ti 4Gb/ 16Gb/ 1Tb)")
    assert res["ram"] == 16


def test_ram_glued_keyword_formats():
    assert LaptopParser.regex_parse("i5-13500H RAM16GB MNVe512GB", "Asus")["ram"] == 16
    assert LaptopParser.regex_parse("", "MacBook Air M1/512GB/8RAM")["ram"] == 8


def test_ram_ddr_keyword():
    res = LaptopParser.regex_parse("", "MSI! i5 12450H/RTX 4050 6Gb/DDR5 16Gb/SSD 512Gb")
    assert res["ram"] == 16


def test_ram_keyword_skips_cpu_model_digit():
    """'i5-1035g1 ram 8gb': the '1' from the CPU model must not bind to 'ram'."""
    res = LaptopParser.regex_parse("intel core i5-1035g1 ram 8gb ssd 256gb", "Acer")
    assert res["ram"] == 8


def test_ram_ignores_romanian_gpu_memory():
    """'memorie dedicată' is GPU VRAM, not system RAM."""
    res = LaptopParser.regex_parse(
        "video gtx 1660 ti cu 6 gb memorie dedicată. ram: 16 gb ddr4", "MSI Leopard"
    )
    assert res["ram"] == 16


def test_ram_ddr_config_list_not_matched_across_slash():
    """Shop price lists like 'ddr3 / 128gb ssd' must not yield RAM=128."""
    res = LaptopParser.regex_parse("8192mb (2x4gb) ddr3 / 128gb ssd = 2 490mdl", "HP Probook")
    assert res["ram"] != 128


def test_m_chip_requires_apple_title():
    """A 'как macbook' comparison plus an 'ssd m2' must not make a Xiaomi an Apple."""
    res = LaptopParser.regex_parse(
        "ультрабук как macbook, ssd m2 256gb, intel core i5-8250u", "Xiaomi mi 13.3"
    )
    assert "m2" not in res["cpu"]


def test_m_chip_kept_for_real_macbook():
    res = LaptopParser.regex_parse("apple m2 8gb ram 256gb ssd", "MacBook Air 13 M2")
    assert res["cpu"] == "m2"


def test_warranty_year_not_release_year():
    res = LaptopParser.regex_parse("i5-1135g7 8gb ram 256gb, гарантия до 2025 года", "Lenovo")
    assert res["year_est"] == 2021  # from the CPU generation, not the warranty


def test_literal_backslash_n_healed():
    """Already-scraped ads store stringified dicts where literal \\n glues
    letters to sizes ("hx\\n16gb ram") and hides specs from every regex."""
    res = LaptopParser.regex_parse(
        r"{'ru': 'core i9 13980hx\n16gb ram\nssd 1tb\nrtx 4070 8gb'}", "Asus ROG Strix"
    )
    assert res["ram"] == 16
    assert res["ssd"] == 1024


def test_cpu_g7_suffix_kept():
    """i5-1135G7-style models must keep the full suffix for benchmark lookup."""
    res = LaptopParser.regex_parse("i7-1165g7, 16gb ram, 512gb ssd", "Lenovo ThinkPad")
    assert res["cpu"] == "i7-1165g7"


def test_slitted_and_standalone_ssd():
    # 256gb slitted
    res1 = LaptopParser.regex_parse("8gb ram 256gb ssd", "Lenovo")
    assert res1["ssd"] == 256

    # 1tb standalone
    res2 = LaptopParser.regex_parse("16GB / 1TB", "Asus Vivobook")
    assert res2["ssd"] == 1024

    # 256gb standalone
    res3 = LaptopParser.regex_parse("8gb ram 256gb", "Dell Inspiron")
    assert res3["ssd"] == 256
