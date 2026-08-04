# Contributing to NotebookBuy

Thank you for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# 1. Clone the repo
git clone https://github.com/zabrodschiipavel-sketch/notebookbuy.git
cd notebookbuy

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS / Linux

# 3. Install all dependencies (runtime + dev)
pip install -e ".[dev]"

# 4. Copy the env template and add your API key
cp .env.example .env
```

## Running the Pipeline

```bash
# Fetch ads from 999.md
python lappars.py --once

# Analyze and score
python laptop_analyzer_v3.py

# Launch the dashboard
streamlit run laptop_dashboard.py
```

## Code Style

We use **[Ruff](https://docs.astral.sh/ruff/)** for linting and formatting.
Configuration lives in `pyproject.toml`.

```bash
# Check for lint errors
ruff check .

# Auto-fix what can be fixed
ruff check . --fix
```

## Running Tests

```bash
pytest -v
```

All tests must pass before submitting a pull request.

## Submitting Changes

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/my-improvement`.
3. Make your changes and add tests where appropriate.
4. Run `ruff check .` and `pytest` — both must be clean.
5. Commit with a descriptive message and open a Pull Request.

## Reporting Bugs

Open an issue with:
- Steps to reproduce
- Expected vs actual behavior
- Python version and OS
