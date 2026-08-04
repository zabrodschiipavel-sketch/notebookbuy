# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Brave Search grounds the world-price and Notebookcheck lookups (`web_search.py`,
  `BRAVE_API_KEY`). Gemini's own `google_search` tool drew on the same per-model
  quota as spec extraction, returned an answer with no visible sources, and
  produced the 12%/15% "Notebookcheck ratings" that `plausible_nbc_score` exists
  to throw away. Brave has a separate budget (free tier: 1 req/s, 2000/month)
  and returns retail and review pages; the model is asked to read prices out of
  those snippets rather than recall them, and to return 0 when the answer is not
  there. Without the key both figures degrade to the component formula, which
  the digest now marks as an estimate.

- «Подешевели» block in the digest. The scraper detects every price drop on
  each run (141 on a typical morning) and until now only wrote them to a log
  nobody reads — the single most actionable buy signal was computed daily and
  discarded. Drops are cross-checked against analysed listings and bounded by
  `PRICE_DROP_MIN_PCT`/`PRICE_DROP_MAX_PCT`, because the raw list is mostly a
  seller correcting a 111111 MDL typo into a "-99% drop".

### Removed

- `AIService.google_search_json` and the Gemini `google_search` grounding tool,
  superseded by the Brave-backed lookups.
- `gemini-2.5-flash` from the review fallback chain — it now answers
  `404 NOT_FOUND: no longer available to new users`. It was the chain's last
  resort, so the only thing standing between the digest and an unreviewed send
  was the model ahead of it still working.

### Changed

- Review model defaults changed after benchmarking the candidates on real
  listings from past digests (4 planted scams, 3 legitimate deals):

  | model | scams caught | false positives | time |
  |---|---|---|---|
  | `gemini-3.1-flash-lite-preview` | 2/4 | 0/3 | 2.2s |
  | `gemini-flash-latest` | 3/4 | 0/3 | 6.6s |
  | `gemini-3.5-flash` | 3/4 | 0/3 | 8.3s |
  | `gemini-3.6-flash` | 3/4 | 0/3 | 9.3s |
  | `gemini-2.5-flash` | — | — | dead (404) |

  `GEMINI_PRO_MODEL` is renamed to `GEMINI_REVIEW_MODEL` and defaults to
  `gemini-flash-latest` instead of `gemini-pro-latest`, whose free-tier quota is
  0 — every run spent an attempt on a guaranteed failure before falling through.
  The chain is now `gemini-flash-latest → gemini-3.6-flash →
  gemini-3.1-flash-lite`: an alias leads so it cannot go stale, a pinned model
  backs it up so a repointed alias cannot take the whole chain down.
- `GEMINI_MODEL` defaults to `gemini-flash-lite-latest` rather than the pinned
  `gemini-3.1-flash-lite-preview`. On extraction every flash-lite generation
  returned identical specs in ~0.9s, so the only thing pinning bought was
  eventual retirement of the preview alias.

### Changed

- Estimated numbers are no longer presented as researched data. When the world
  price or the Notebookcheck rating comes from the component formula the digest
  marks it `≈` and relabels it (`vs расчёт`, `Класс` instead of `vs World`,
  `NBC Score`), with one footnote explaining the sign. Across 146 past digests
  not a single estimate was marked, while `NBC Score` took only 16 distinct
  values — 65 of them an impossible flat `100%`.
- Repeats are held back. A listing already shown at an unchanged price waits
  `DIGEST_REPEAT_COOLDOWN_DAYS` (default 7) before it can return; any price
  move makes it eligible immediately, and new listings are ordered ahead of
  repeats. Held-back entries are added back when the digest would otherwise
  shrink below `DIGEST_MIN_DEALS`. Two thirds of past digest slots went to
  listings already seen unchanged, one of them for 22 days straight.
- The city section (`DIGEST_REGION`, default Бельцы) renders only when it has
  listings. It sat empty in 133 of 146 digests, spending a heading and a
  "nothing found" line every day on 1.4% of the listings.

### Fixed

- **Gemini calls were being rate-limited into uselessness on every run.** The
  free tier grants 15 requests per minute *per model*; the worker pool spent
  that budget in about five seconds and every later call returned 429, while
  the retry back-off waited 1–4 s against the ~16 s the API asked for. Across
  the daily runs sampled between 2026-06-12 and 2026-08-04 only ~18 of ~160
  calls succeeded, so 25–30 ads a day were dropped from the ranking and the
  world-price and Notebookcheck lookups never returned anything. Calls now go
  through a shared per-model `RateLimiter` (`GEMINI_RPM_LIMIT`, default 12) and
  the back-off honours the server's own `retryDelay`.
- `get_price_drops` anchored "first" and "last" price on `recorded_at`. Two
  snapshots written inside the same clock tick share a timestamp, so
  `MIN(recorded_at)` and `MAX(recorded_at)` matched the same rows and the drop
  collapsed to 0%. Ordering is now anchored on the autoincrement id. The
  existing test only caught this on a coarse clock (it passed on CI, failed on
  Windows); a timestamp-independent regression test was added.
- Failed world-price / Notebookcheck lookups were never written to the cache,
  so `pricehistory_cache.json` and `notebookcheck_cache.json` were never
  created — the Actions cache and the report artifact had nothing to store, and
  every run re-asked the same unanswered questions. Misses are now remembered
  for `EXTERNAL_MISS_TTL_DAYS` (default 7).

### Changed

- AI extraction logs an explicit `parsed / failed` tally and raises a GitHub
  Actions warning annotation when it drops more than 20% of a batch — the daily
  workflow reported success for two months while most of its AI work failed.
- `plotly` moved from `<6` (stuck on the end-of-life 5.24.1) to `>=6,<7`, and
  the deprecated `use_container_width=` was replaced with `width="stretch"`,
  which requires `streamlit>=1.50`.
- Added `.github/dependabot.yml` (pip + github-actions, monthly) and a weekly
  schedule plus `workflow_dispatch` on CI: dependencies are unpinned and the
  daily job installs them fresh every morning, so drift needs to be caught
  without waiting for the next commit.
- Repository URLs across README, `pyproject.toml`, `CONTRIBUTING.md`,
  `SECURITY.md`, the issue-template config and the publish script pointed at
  `pravel-no/notebookbuy`, which stopped receiving pushes on 2026-06-10; the
  README CI badge was therefore reporting a different repository's status.
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

[1.0.0]: https://github.com/zabrodschiipavel-sketch/notebookbuy/releases/tag/v1.0.0
