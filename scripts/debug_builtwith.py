"""Single-domain BuiltWith scraper debug runner.

Usage (on VPS):
  cd /opt/funnel-intel && .venv/bin/python3 -m scripts.debug_builtwith madmuscles.com

Runs scrape_relationships on one domain with DEBUG logging so you can see
every subprocess call and timing. Dumps the full page state on empty results.
"""
from __future__ import annotations
import logging
import sys

from backend.worker.builtwith_scraper import scrape_relationships, _run


def main():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-7s %(name)-40s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy third-party loggers
    for name in ("httpx", "httpcore", "urllib3", "anthropic", "browser_use"):
        logging.getLogger(name).setLevel(logging.WARNING)

    if len(sys.argv) < 2:
        print("usage: debug_builtwith.py <domain>", file=sys.stderr)
        sys.exit(2)

    domain = sys.argv[1]
    print(f"=== Scraping BuiltWith for {domain} ===")
    try:
        rows = scrape_relationships(domain)
    except Exception as e:
        print(f"=== FAILED: {type(e).__name__}: {e} ===")
        sys.exit(1)

    print(f"=== Got {len(rows)} relationship rows ===")
    for r in rows[:5]:
        print(f"  {r}")
    if len(rows) > 5:
        print(f"  ... and {len(rows) - 5} more")

    # Bonus: dump the current page title and a chunk of body text so we
    # can sanity-check what BuiltWith actually served us.
    try:
        probe = _run([
            "eval",
            "JSON.stringify({title: document.title, "
            "html_len: document.documentElement.outerHTML.length, "
            "body_preview: document.body.innerText.slice(0, 600)})"
        ], timeout=30)
        if probe.startswith("result: "):
            probe = probe[len("result: "):]
        print(f"=== Page state ===\n{probe}")
    except Exception as e:
        print(f"=== page-state probe failed: {e} ===")


if __name__ == "__main__":
    main()
