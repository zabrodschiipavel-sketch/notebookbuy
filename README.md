# 💻 NotebookBuy

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/zabrodschiipavel-sketch/notebookbuy/actions/workflows/ci.yml/badge.svg)](https://github.com/zabrodschiipavel-sketch/notebookbuy/actions/workflows/ci.yml)

**Find the best laptop deals on [999.md](https://999.md)** — scrape listings via GraphQL, score the hardware against Passmark benchmarks, and get a ranked shortlist in Telegram every morning.

A GitHub Actions cron runs the whole pipeline daily and sends the result to a Telegram chat. The Streamlit dashboard is there for digging into the accumulated data afterwards.

> **Disclaimer:** NotebookBuy is not affiliated with 999.md. Use respectful request rates and comply with the site's terms of service. Scraping is for personal research only.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **GraphQL Scraper** | Direct API queries — no HTML parsing overhead |
| **Hardware Scoring** | Passmark CPU + GPU fuzzy-match benchmarks |
| **AI Spec Extraction** | A free OpenRouter model reads the ads regex can't parse, ten per request |
| **Real World Prices** | Brave Search finds retail and review pages; the model reads the figures out of them |
| **Scam Screening** | Heuristics plus an AI pass over the shortlist drop parsing garbage and locked/stolen units |
| **Honest Labelling** | Anything computed rather than found is marked `≈` and named an estimate |
| **Telegram Digest** | Daily shortlist with price-drop alerts; repeats are held back |
| **Price Tracking** | Every price change is recorded; history visualized per ad |
| **Dynamic FX Rates** | USD/EUR → MDL rates auto-fetched & cached for 24 h |
| **Dashboard** | Streamlit UI with scatter plots, filters, and CSV/JSON export |

---

## 🏗️ Architecture

```mermaid
graph TD
    A[999.md GraphQL API] -->|Fetch| B[lappars.py]
    B -->|Upsert| C[(SQLite)]
    C -->|Read| D[laptop_analyzer_v3.py]
    D --> E[parser.py — Regex]
    D --> F[benchmarks.py — Passmark]
    D --> G[ai_service.py]
    G --> O[openrouter.py — OpenRouter]
    G --> W[web_search.py — Brave]
    D --> H[scoring.py — Value Score]
    H -->|Write| C
    C -->|Feed| T[send_telegram.py — Digest]
    C -->|Feed| I[laptop_dashboard.py — Streamlit]
```

Regex parses what it can; only ads it fails on go to the AI model. Passmark supplies the raw
performance numbers. World prices and Notebookcheck ratings are looked up through Brave
and read out of the returned snippets — when a lookup finds nothing, a component-based
formula fills in and **the digest marks that value as an estimate** rather than passing
it off as market data.

## 📂 Project Structure

```
notebookbuy/
├── lappars.py              # 999.md GraphQL scraper + price tracker
├── laptop_analyzer_v3.py   # Spec extraction + benchmark scoring pipeline
├── send_telegram.py        # Digest: ranking, repeat suppression, price drops
├── laptop_dashboard.py     # Streamlit analytics dashboard
├── parser.py               # Precompiled regex for CPU/GPU/RAM/SSD
├── benchmarks.py           # Passmark data fetch, cache, and fuzzy search
├── scoring.py              # Value-score formula, classification, SSD heuristic
├── estimation.py           # Shared component-based fallback price/score
├── ai_service.py           # Batched spec extraction, deal review, snippet reading
├── openrouter.py           # OpenRouter client: fallback chain, JSON, catalog check
├── web_search.py           # Brave Search client for price/review lookups
├── currency.py             # Dynamic exchange rate fetching
├── db.py                   # SQLite schema, migrations, context manager
├── app_config.py           # Env-based runtime configuration
├── retry_utils.py          # Rate limiter + retry that honours server back-off
├── run_summary.py          # Per-step table on the GitHub Actions run page
├── state_snapshot.py       # Pack/restore DB + caches to the pipeline-data branch
├── requirements-lock.txt   # Pinned deps for the daily workflow
├── launcher.py             # PyInstaller .exe entry point
├── query_999.graphql       # GraphQL query for 999.md ads
├── pyproject.toml          # Project metadata, dependencies, ruff, pytest
├── .env.example            # Template for environment variables
├── tests/                  # see `pytest -v`
│   ├── test_parser.py          # Regex extraction
│   ├── test_scoring.py         # Scoring, classification & SSD heuristic
│   ├── test_estimation.py      # Fallback price/score estimation
│   ├── test_benchmarks.py      # Passmark fuzzy-match lookup
│   ├── test_lappars.py         # Price-tracking upsert & GraphQL parse
│   ├── test_send_telegram.py   # Digest selection, novelty, drops, formatting
│   ├── test_ai_service.py      # Batching, deal review, Brave lookups
│   ├── test_openrouter.py      # Fallbacks, retries, JSON parsing (fake transport)
│   ├── test_web_search.py      # Brave client (fake transport, no network)
│   ├── test_retry_utils.py     # Rate limiter & back-off
│   ├── test_analyzer_cache.py  # External-lookup cache markers
│   ├── test_analyzer_pipeline.py # LaptopAnalyzer.run() end to end
│   ├── test_state_snapshot.py  # State pack/restore and its guards
│   ├── test_currency.py        # Exchange rate fallback
│   └── test_db.py              # Schema creation & migration
└── .github/
    ├── dependabot.yml      # Monthly pip + actions updates
    └── workflows/
        ├── ci.yml          # Lint + test on push/PR and weekly
        └── daily_scrape.yml # Scrape → analyze → Telegram, daily at 06:00 UTC
```

---

## ⚡ Quick Start

### 1. Install

```bash
git clone https://github.com/zabrodschiipavel-sketch/notebookbuy.git
cd notebookbuy
python -m venv .venv
```

Activate the virtual environment:

- **Windows:** `.venv\Scripts\activate`
- **macOS / Linux:** `source .venv/bin/activate`

Then install dependencies:

```bash
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

Every key is **optional** — with none of them the pipeline still scrapes, parses with
regex and scores against Passmark (Passmark data downloads on the first analyzer run).
Each key buys back a specific capability:

| Variable | What it enables | Without it |
|----------|-----------------|------------|
| `OPENROUTER_API_KEY` | Spec extraction for ads regex can't parse; scam review of the shortlist | Those ads drop out of the ranking; no AI review |
| `BRAVE_API_KEY` | Real world prices and Notebookcheck ratings | Both come from the component formula, marked `≈` |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Sending the digest | `send_telegram.py` exits with a config error |

#### AI models

Every AI call goes to OpenRouter and asks for JSON, with a fallback chain resolved
server-side inside the same request:

| Role | Default | Why |
|------|---------|-----|
| Primary | `nvidia/nemotron-3-super-120b-a12b:free` | Free **and** enforces structured outputs. Nemotron 3 Ultra ranks higher on the free charts but ignores `response_format`, and every call here is JSON. |
| Fallback | `meta/muse-spark-1.3-contributor` | Answers when the primary is down, gone or out of quota. Not a `:free` variant: it bills per token — cents a month at this volume — and has rate limits of its own. |

Three account settings decide whether the defaults work:

- **Privacy** — allow endpoints that may train on prompts. Free models and the
  Contributor tier do; otherwise OpenRouter refuses them with a 404.
- **Credits** — the paid fallback needs a positive balance.
- **Daily cap** — free models allow 20 requests/minute and **50 requests/day**, or
  1000/day once $10 of credits has ever been bought. Extraction sends ten ads per
  request, so a morning run needs roughly 10–30 requests.

The free roster changes month to month. `python openrouter.py` prints the free models
that currently support structured outputs and flags any configured model that has
left the catalog; the daily workflow runs it and raises a warning on the run.

Brave allows **1 req/s and 2000/month**, far more than a daily run needs. See
`.env.example` for the full list of tunables.

### 3. Run

```bash
# Fetch latest ads (--region all covers Moldova; default is balti).
# Pages through the category up to SCRAPE_MAX_ADS (3000).
python lappars.py --once --region all

# Analyze & score
python laptop_analyzer_v3.py

# Send the digest to Telegram
python send_telegram.py

# Launch dashboard
streamlit run laptop_dashboard.py
```

---

## 📬 The Daily Digest

`.github/workflows/daily_scrape.yml` runs the pipeline at 06:00 UTC and posts a
shortlist to Telegram.

- **State survives a quiet week.** The database and lookup caches live in the Actions
  cache, which GitHub evicts after 7 days without access. After each successful scrape
  `state_snapshot.py` also publishes them to the `pipeline-data` branch (one commit,
  overwritten daily), and a run that misses the cache restores from there. It refuses to
  publish a database that shrank below half of the previous snapshot — that is a broken
  restore, and it must not overwrite the only good copy.
- **Failures fail the run.** A scrape that fetched nothing, or a digest that was not
  delivered, exits non-zero instead of carrying on over yesterday's data or staying green
  through a week of silence. Each step writes its numbers (ads fetched, sent to AI,
  answered by the fallback model, messages delivered) to the run's summary page.
- **Pinned dependencies.** The daily job installs `requirements-lock.txt`; the weekly CI
  run installs unpinned versions, so drift shows up there first.

What the digest does beyond ranking by score:

- **Marks estimates.** A figure found by search reads `-21% vs мировая цена` and
  `NBC Score: 88%`. A figure computed by the component formula reads `≈-19% vs расчёт`
  and `Класс: ≈78%`, with a footnote explaining the sign. An estimate is never dressed
  up as market data.
- **Holds back repeats.** A listing already sent at an unchanged price waits
  `DIGEST_REPEAT_COOLDOWN_DAYS` (7) before it can return; any price move makes it
  eligible again immediately, and never-seen listings rank ahead of repeats. If that
  leaves too few entries, held-back ones come back — the digest never ends up shorter
  than it would have been.
- **Reports price drops.** A separate block lists laptops that got cheaper, filtered
  against sellers correcting a typo (a "-99% drop") and spare-parts ads.
- **Screens for scams.** Heuristics flag suspiciously cheap Apple Silicon (MDM/iCloud
  locks); an AI pass over the shortlist drops listings whose specs contradict the
  title and warns about the rest.

---

## 🧮 How Scoring Works

Each laptop gets a **Value Score** = performance-per-MDL index.

```
tech_pts = (CPU_bench × 0.55 + GPU_bench × 0.35 + (RAM×150 + SSD_bonus) × 0.10)
         × RAM_multiplier × age_penalty × broken_penalty

value_score = tech_pts / price × 100
```

- **CPU / GPU benchmarks** — looked up from [Passmark](https://www.cpubenchmark.net/) via fuzzy name matching.
- **RAM multiplier** — 1.0 at 16 GB, scaling from 0.6 (4 GB) to 1.18 (48 GB); 0.05 when RAM is unknown.
- **SSD bonus** — 2 points per GB, capped at 4000. A missing size falls back to a size plausible for the model year.
- **Age penalty** — 12 % per year from current; floors at 0.05.
- **Broken penalty** — ×0.05 if the ad is marked as spare parts.
- **Low-end guards** — below `MIN_ACCEPTABLE_TECH_PTS` the score is squashed quadratically, and a CPU under `MIN_CPU_SCORE` halves it. Both exist to stop a cheap weak machine from topping a per-MDL ranking.
- Higher **value_score** → better deal. The digest only sends listings scoring 100 or more.

---

## 🧪 Testing & Linting

```bash
pytest -v
```

```bash
ruff check .
```

No test touches the network: the Brave client, the OpenRouter calls and the Telegram send
are all exercised through fakes. Configuration for both tools lives in `pyproject.toml`.
CI runs them on Python 3.10, 3.12 and 3.14 for every push and pull request, plus weekly
— dependencies are unpinned and the daily job installs them fresh each morning, so drift
gets caught without waiting for the next commit.

---

## 📦 Building an .exe

```bash
pyinstaller laptop_finder.spec
```

The standalone executable will be in `dist/`.

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and PR guidelines.

---

## 📄 License

[MIT](LICENSE)
