"""Tests for price-tracking upsert logic and price-drop detection."""
import sqlite3

import pytest

import lappars
from db import _initialized_dbs, init_database


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "ads.db")
    _initialized_dbs.discard(path)
    init_database(path)
    yield path
    _initialized_dbs.discard(path)


def _ad(ad_id: int, price: float, title: str = "Laptop") -> dict:
    return {
        "ID": ad_id,
        "Заголовок": title,
        "Цена": price,
        "Валюта": "MDL",
        "Ссылка": f"https://999.md/ru/{ad_id}",
        "HTML_страницы": "description text",
        "Изображение": "",
    }


def test_new_then_unchanged(db):
    with sqlite3.connect(db) as conn:
        cur = conn.cursor()
        assert lappars.save_or_update_ad(cur, _ad(1, 10000)) == "new"
        assert lappars.save_or_update_ad(cur, _ad(1, 10000)) == "unchanged"
        conn.commit()


def test_price_drop_then_rise(db):
    with sqlite3.connect(db) as conn:
        cur = conn.cursor()
        lappars.save_or_update_ad(cur, _ad(1, 10000))
        assert lappars.save_or_update_ad(cur, _ad(1, 8000)) == "price_drop"
        assert lappars.save_or_update_ad(cur, _ad(1, 9000)) == "price_rise"
        conn.commit()


def test_sub_one_percent_change_is_unchanged(db):
    """Changes under 1% are treated as FX noise, not a real price move."""
    with sqlite3.connect(db) as conn:
        cur = conn.cursor()
        lappars.save_or_update_ad(cur, _ad(1, 10000))
        assert lappars.save_or_update_ad(cur, _ad(1, 10050)) == "unchanged"
        conn.commit()


def test_parse_graphql_ad_basic():
    ad = {
        "id": "42",
        "title": "  Lenovo Legion  ",
        "description": {"value": "Gaming laptop"},
        "ssd_feature": {"value": {"translated": "512 GB"}},
        "price": {"value": "1500 €"},
        "images": {"value": '["abc/def"]'},
    }
    parsed = lappars.parse_graphql_ad(ad)
    assert parsed["id"] == 42
    assert parsed["title"] == "Lenovo Legion"
    assert parsed["url"] == "https://999.md/ru/42"
    assert "[SSD: 512 GB]" in parsed["body"]
    assert parsed["body"].startswith("Gaming laptop")
    assert parsed["image_url"] == "https://i.999.md/m/abc/def.jpg"
    # 1500 EUR converted to MDL must exceed the raw amount.
    assert parsed["price"] > 1500


def test_parse_graphql_ad_unwraps_translations_dict():
    """A dict-valued description must yield the translated text, not a
    Python-repr blob ("{'ro': ..., 'ru': ...}") with literal \\n sequences —
    97% of stored ads were affected."""
    ad = {
        "id": "43",
        "title": "Asus ROG",
        "description": {"value": {
            "ro": "text ro", "ru": "текст ру", "translated": "core i9\n16gb ram",
        }},
    }
    parsed = lappars.parse_graphql_ad(ad)
    assert parsed["body"] == "core i9\n16gb ram"
    assert "{'ro'" not in parsed["body"]


def test_parse_graphql_ad_usd_and_plain():
    usd = lappars.parse_graphql_ad({"id": "1", "price": {"value": "$1000"}})
    plain = lappars.parse_graphql_ad({"id": "2", "price": {"value": "9999 lei"}})
    assert usd["price"] > 1000          # USD -> MDL
    assert plain["price"] == 9999.0     # already MDL, no conversion


def test_parse_graphql_ad_missing_fields():
    parsed = lappars.parse_graphql_ad({"id": "7"})
    assert parsed["price"] == 0.0
    assert parsed["body"] == ""
    assert parsed["image_url"] == ""


def test_get_price_drops_reports_drop(db):
    with sqlite3.connect(db) as conn:
        cur = conn.cursor()
        lappars.save_or_update_ad(cur, _ad(1, 10000, "Dropper"))
        lappars.save_or_update_ad(cur, _ad(1, 8000, "Dropper"))  # -20%
        lappars.save_or_update_ad(cur, _ad(2, 5000, "Stable"))   # single record
        conn.commit()

    drops = lappars.get_price_drops(min_drop_pct=5.0, db=db)
    by_id = {d["ad_id"]: d for d in drops}
    assert 1 in by_id
    assert by_id[1]["first_price"] == 10000
    assert by_id[1]["last_price"] == 8000
    assert by_id[1]["drop_pct"] == 20.0
    # Single-record ad must not appear (needs > 1 history row).
    assert 2 not in by_id


def test_get_price_drops_with_identical_timestamps(db):
    """Snapshots sharing a recorded_at must still yield first/last correctly.

    On a coarse system clock two consecutive writes land on the same timestamp;
    ordering by recorded_at then matched both rows as first *and* last and the
    drop collapsed to 0%. Ordering is anchored on the autoincrement id instead.
    """
    stamp = "2026-01-01 10:00:00.000000"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO ads (id, title, price, url) VALUES (1, 'Dropper', 8000, 'u')"
        )
        conn.executemany(
            "INSERT INTO price_history (ad_id, price, recorded_at) VALUES (?, ?, ?)",
            [(1, 10000, stamp), (1, 8000, stamp)],
        )
        conn.commit()

    by_id = {d["ad_id"]: d for d in lappars.get_price_drops(min_drop_pct=5.0, db=db)}
    assert by_id[1]["first_price"] == 10000
    assert by_id[1]["last_price"] == 8000
    assert by_id[1]["drop_pct"] == 20.0


# ---------- pagination ----------

class FakeCatalog:
    """Serves pages of a fake category; optional failure at a given skip."""

    def __init__(self, total: int, fail_at: int | None = None, shift: int = 0):
        self.ads = [{"id": str(i)} for i in range(total)]
        self.fail_at = fail_at
        self.shift = shift  # repeat this many ads at each page start
        self.calls: list[tuple[int, int]] = []

    def __call__(self, region, skip, limit):
        self.calls.append((skip, limit))
        if self.fail_at is not None and skip >= self.fail_at:
            raise RuntimeError("HTTP 502")
        start = max(0, skip - self.shift)
        return self.ads[start:start + limit], len(self.ads)


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(lappars.time, "sleep", lambda s: None)
    monkeypatch.setattr("retry_utils.time.sleep", lambda s: None)


def test_scrape_pages_through_the_whole_category(no_sleep):
    """One 500-ad request used to be the whole scrape."""
    catalog = FakeCatalog(total=450)
    ads = lappars.fetch_all_ads("all", page_size=200, max_ads=3000, fetch=catalog)
    assert len(ads) == 450
    assert [skip for skip, _ in catalog.calls] == [0, 200, 400]


def test_scrape_stops_at_max_ads(no_sleep):
    catalog = FakeCatalog(total=1000)
    ads = lappars.fetch_all_ads("all", page_size=200, max_ads=300, fetch=catalog)
    assert len(ads) == 300
    assert catalog.calls == [(0, 200), (200, 100)]


def test_scrape_dedupes_ads_repeated_across_pages(no_sleep):
    """New listings shift the order while paging, so pages overlap."""
    catalog = FakeCatalog(total=300, shift=5)
    ads = lappars.fetch_all_ads("all", page_size=100, max_ads=3000, fetch=catalog)
    ids = [a["id"] for a in ads]
    assert len(ids) == len(set(ids))


def test_first_page_failure_raises(no_sleep):
    with pytest.raises(RuntimeError):
        lappars.fetch_all_ads("all", page_size=100, fetch=FakeCatalog(total=300, fail_at=0))


def test_later_page_failure_keeps_what_was_fetched(no_sleep):
    ads = lappars.fetch_all_ads("all", page_size=100, fetch=FakeCatalog(total=300, fail_at=200))
    assert len(ads) == 200


def test_failed_scrape_fails_the_run(monkeypatch, db):
    """Exit 0 on a failed scrape let the digest go out over yesterday's data."""
    monkeypatch.setattr(lappars, "DB_NAME", db)
    monkeypatch.setattr(lappars, "fetch_and_process", lambda region: None)
    monkeypatch.setattr("sys.argv", ["lappars.py", "--once"])
    assert lappars.main() == 1
    monkeypatch.setattr(lappars, "fetch_and_process", lambda region: {"new": 1})
    assert lappars.main() == 0
