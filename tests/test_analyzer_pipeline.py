"""End-to-end test of LaptopAnalyzer.run(): DB in, ranking out (no network)."""
import datetime
import json
import sqlite3

import pytest

import estimation
import laptop_analyzer_v3 as analyzer
from ai_service import AIService
from db import _initialized_dbs
from parser import LaptopParser


class FakeBench:
    def __init__(self, scores: dict[str, int]):
        self.scores = scores

    def search(self, name: str) -> int:
        return next((v for k, v in self.scores.items() if k in str(name).lower()), 0)


ADS = [
    # (id, title, price, description)
    (1, "Lenovo IdeaPad i5-1235U 16gb ram 512gb", 6000, "ноутбук 2023 года"),
    (2, "Ноутбук мощный, для игр", 9000, "видеокарта летает"),  # regex fails → AI
    (3, "Куплю ноутбук дорого", 5000, "куплю"),                   # buying ad, skipped
    (4, "Dell Latitude celeron n2840 4gb ram", 1500, "старый"),   # below the quality bar
]


@pytest.fixture
def run_analyzer(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    _initialized_dbs.discard(db)
    monkeypatch.chdir(tmp_path)  # the text report is written to the cwd
    monkeypatch.setattr(analyzer, "DB_NAME", db)
    monkeypatch.setattr(analyzer, "ENABLE_EXTERNAL_LOOKUPS", False)
    monkeypatch.setattr(estimation, "_components_cache", {})

    now = datetime.datetime.now()
    analyzer.init_database(db)
    with sqlite3.connect(db) as conn:
        for ad_id, title, price, desc in ADS:
            conn.execute(
                "INSERT INTO ads (id, title, price, currency, url, description, parsed_at) "
                "VALUES (?, ?, ?, 'MDL', ?, ?, ?)",
                (ad_id, title, price, f"https://999.md/ru/{ad_id}", desc, now),
            )

    calls = []

    def chat(system, user, schema, **kwargs):
        listings = json.loads(user.split("\n", 1)[1])
        calls.append([item["id"] for item in listings])
        return {"laptops": [
            {"id": item["id"], "cpu": "AMD Ryzen 7 5800H", "gpu": "NVIDIA GeForce RTX 3060",
             "ram": 16, "ssd": 512, "is_broken": False}
            for item in listings
        ]}, "fake-model"

    lap = analyzer.LaptopAnalyzer.__new__(analyzer.LaptopAnalyzer)
    lap.db = analyzer.DatabaseManager(db)
    lap.cpu_bench = FakeBench({"i5-1235u": 13000, "ryzen 7 5800h": 21000, "celeron": 1000})
    lap.gpu_bench = FakeBench({"rtx 3060": 12000})
    lap.ai = AIService(chat=chat)
    lap.parser = LaptopParser()
    return lap, db, calls, tmp_path


def test_run_ranks_regex_and_ai_parsed_ads(run_analyzer):
    lap, db, calls, tmp_path = run_analyzer
    ranked = lap.run()

    assert calls == [["2"]], "only the ad the regex could not parse goes to the AI"
    ids = {r["id"] for r in ranked}
    # The 12th-gen i5 used to be dated 2009 and dropped here.
    assert ids == {"1", "2"}
    assert list(tmp_path.glob("top_laptops_*.txt")), "the text report is written"

    with sqlite3.connect(db) as conn:
        cached = dict(conn.execute("SELECT id, year_est FROM analysis_cache").fetchall())
    assert cached["1"] == 2023
    assert "3" not in cached, "buying ads are never analysed"


def test_second_run_uses_the_cache(run_analyzer):
    lap, _, calls, _ = run_analyzer
    lap.run()
    lap.run()
    assert calls == [["2"]], "an unchanged ad is not sent to the AI twice"


def test_ai_extraction_is_capped_per_run(run_analyzer, monkeypatch):
    lap, _, calls, _ = run_analyzer
    monkeypatch.setattr(analyzer, "AI_EXTRACT_MAX_ADS", 0)
    ranked = lap.run()
    assert calls == []
    assert {r["id"] for r in ranked} == {"1"}
