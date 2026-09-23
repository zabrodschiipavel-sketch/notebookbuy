"""Run summary for GitHub Actions: a small table per pipeline step.

The daily log runs to thousands of lines; the numbers that tell a good run from
a degraded one (ads scraped, ads the AI dropped, whether the fallback model
answered, messages delivered) belong on the run's summary page instead.
Outside Actions this is a no-op.
"""
import logging
import os


log = logging.getLogger(__name__)


def write(title: str, rows: dict[str, object]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### {title}", "", "| | |", "|---|---|"]
    lines += [f"| {key} | {value} |" for key, value in rows.items()]
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
    except OSError as e:
        log.warning("Could not write the run summary: %s", e)
