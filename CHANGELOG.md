# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Unified the component-based fallback price/score logic into a single
  `estimation.py` shared by the analyzer and the Telegram notifier, which had
  silently diverged.
- Centralized the "missing SSD" heuristic in `scoring.infer_ssd_gb` (was
  duplicated across parser, scoring, and notifier).
- Decomposed `lappars.fetch_and_process` into small, unit-tested helpers
  (`parse_graphql_ad`, `_html_fallback_description`).
- `send_telegram.py` now uses `logging` instead of `print`.
- The Telegram digest renders with Telegram HTML instead of Markdown, with all
  listing-supplied text escaped — titles containing `*`, `_`, `[` or `<` can
  no longer break the markup or fail the send.
- The dashboard's duplicated fallback price/score tiers were removed in favor
  of the shared `estimation.py` + `components_db.json` (the local copy had
  already drifted from what the analyzer and notifier used).
- The world-price/NBC cache key is built by a single helper
  (`estimation.external_cache_key`) shared by the analyzer, dashboard, and
  notifier.
- `daily_scrape.yml` uses explicit `actions/cache/restore` + `cache/save`
  (with `if: always()`) instead of two conflicting `actions/cache` steps, so
  scraped data survives even when a later step fails.

### Fixed

- The GraphQL scraper stored descriptions as a stringified translations dict
  (`"{'ro': ..., 'ru': ...}"` with literal `\n`) — 97% of saved ads were
  affected and the glued text hid specs from every regex. The scraper now
  unwraps the `translated` text, and the parser treats literal `\n` as
  whitespace so already-scraped rows heal on re-analysis.
- Spec extraction bugs found by replaying the parser over 758 real ads
  (RAM improved on 52 ads, CPU on 68, year on 72, SSD on 15):
  - RAM no longer steals storage sizes ("128gb ssd" → 128 GB RAM) or GPU VRAM
    ("RTX 3050Ti 4Gb" → 4 GB RAM); keyword-tied sizes ("RAM16GB", "8RAM",
    "DDR5 32Gb", "озу: 16 гб") are now recognized, with guards for
    "GDDR6 8GB", Romanian "memorie dedicată" (VRAM), digits glued to CPU
    models ("i5-1035g1 ram"), and shop config lists ("ddr3 / 128gb ssd").
  - An M-chip claim now needs Apple context in the title — a body comparison
    ("как macbook") plus an "ssd m2" no longer turns a Xiaomi into an Apple
    with an Apple benchmark score.
  - Warranty years ("гарантия до 2025") are no longer taken as the release
    year; a text year far ahead of the CPU generation falls back to the
    CPU year.
  - i5-1135G7-style suffixes are kept in full (was truncated to "i5", which
    broke the Passmark benchmark lookup).
- The Telegram digest is split into multiple messages when it exceeds
  Telegram's 4096-char limit instead of failing with a 400.
- Urgency wording ("срочно", "urgent", "без торга") no longer flags a listing
  as broken and triggers the heavy value penalty.
- `daily_scrape.yml` no longer references a non-existent `requirements.txt`.
- Register an explicit SQLite datetime adapter to silence the Python 3.12
  deprecation warning without changing stored timestamp format.
- `send_telegram.py` looked up the world-price/NBC caches by ad id while the
  analyzer writes them keyed by CPU+GPU+RAM — the cache never hit and the
  digest always used the rough component fallback.
- `send_telegram.py` no longer crashes with a `TypeError` on listings whose
  year could not be estimated (`year_est` NULL) when evaluating the
  "На запчасти?" risk.

### Added

- The digest deduplicates re-posted listings (same parsed CPU/RAM/SSD plus a
  fuzzy-matching title) — sellers re-post the same laptop under new ad ids and
  it used to occupy several top-5 slots.
- Each digest deal is marked 🆕 or "🔁 В топе с <дата>" with the price delta
  since it was first shown; history persists in `digest_history.json` (kept in
  the Actions cache).
- Notebookcheck ratings from the AI search are validated
  (`estimation.plausible_nbc_score`): hallucinated values like 12%/15% (seen
  flipping to 80% between runs) are replaced by the component-based formula in
  the analyzer, digest, and dashboard.
- CI also runs on Python 3.14; GitHub Actions bumped to current majors
  (checkout v6, setup-python v6, cache v5, upload-artifact v7) — removes the
  Node 20 deprecation warnings.
- AI review of the Telegram digest: the top candidates are passed through
  Gemini Pro (`gemini-pro-latest`, falls back to `gemini-flash-latest` on
  free-tier keys) which excludes scam listings and parsing garbage and adds a
  short verdict line per deal. Configured via `ENABLE_AI_REVIEW`,
  `AI_REVIEW_TOP_N`, `GEMINI_PRO_MODEL`, `GEMINI_REVIEW_FALLBACK_MODEL`.
  Motivated by 3 weeks of digest history where a fake 600-MDL "Maci Brook pro"
  and a "Xiaomi with Apple M2" (regex caught `m.2` from the SSD spec) held
  top-5 spots for days.
- Tests for price tracking, GraphQL parsing, fallback estimation, benchmark
  lookup, and the SSD heuristic (suite: 36 → 63).
- Test coverage for the Telegram digest (`process_deals`, `format_deal`):
  cache-key lookup, missing year, MDM risk, unwanted-ad filtering, region
  parsing, and HTML escaping (suite: 63 → 70).
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` documented in `.env.example`.

## [1.0.0] - 2026-05-17

### Added

- GraphQL scraper for 999.md laptop listings with price history ([lappars.py](lappars.py))
- Analyzer pipeline: regex parsing, Passmark benchmarks, optional Gemini AI ([laptop_analyzer_v3.py](laptop_analyzer_v3.py))
- Streamlit dashboard with filters, charts, and export ([laptop_dashboard.py](laptop_dashboard.py))
- Dynamic USD/EUR → MDL exchange rates with 24h cache ([currency.py](currency.py))
- SQLite schema with migrations ([db.py](db.py))
- CI workflow (Ruff + pytest on Python 3.10 and 3.12)
- Test suite (35 tests) for parser, scoring, currency, and database

### Fixed

- Import `USD_TO_MDL` / `EUR_TO_MDL` from `currency` instead of `scoring`
- GraphQL: use `description: feature(id: 13)` after 999.md removed the `body` field
- SSD parser: treat explicit `gb` units correctly (e.g. `1 gb ssd` → 1 GB, not 1 TB)

[1.0.0]: https://github.com/pravel-no/notebookbuy/releases/tag/v1.0.0
