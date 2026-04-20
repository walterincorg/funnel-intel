"""
Local CLI smoke-test for the Stagehand driver.

Usage:
    python agent.py <funnel_url>                    # record mode, no DB
    python agent.py <funnel_url> --name <label>     # override competitor name

This bypasses the worker loop and the Supabase recipe store — it just drives
Stagehand's autonomous agent once and prints the captured steps + recipe.
Useful for eyeballing what a recording looks like before pointing production
workers at it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from backend.worker.stagehand_driver import run_record, SCAN_TIMEOUT


async def main(url: str, name: str) -> int:
    print(f"\nRecording traversal for {name!r} at {url}\n")
    result, recipe = await asyncio.wait_for(
        run_record(
            funnel_url=url,
            competitor_name=name,
            config=None,
            available_files=None,
            on_progress=lambda entry: print(f"  → {entry.get('message')}"),
            competitor_id=None,
        ),
        timeout=SCAN_TIMEOUT,
    )

    print("\n--- Traversal result ---")
    print(json.dumps(result.as_dict(), indent=2, default=str))
    print("\n--- Recipe (would be saved to Supabase in production) ---")
    print(json.dumps(
        {
            "version": recipe.version,
            "start_url": recipe.start_url,
            "stop_reason": recipe.stop_reason,
            "steps": [s.model_dump() for s in recipe.steps],
        },
        indent=2,
        default=str,
    ))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stagehand traversal smoke test")
    parser.add_argument("url", help="Funnel URL to traverse")
    parser.add_argument("--name", default="CLI smoke test",
                        help="Competitor label (used in prompts/logs)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.url, args.name)))
