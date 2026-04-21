"""Scrape BuiltWith relationship data for a domain.

Flow:
  1. Navigate to builtwith.com/relationships/{domain}
  2. If captcha present: screenshot → crop to grid → Claude Haiku → click two matching cells
  3. Run scraper JS → return list of relationship rows

Each row: {domain, attributeValue, firstDetected, lastDetected, overlapDuration}
Empty list is a valid result (domain has no relationship data on BuiltWith).
"""

from __future__ import annotations
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
from typing import Any

import anthropic
from PIL import Image

from backend.config import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

BROWSER_USE = "/opt/funnel-intel/.venv/bin/browser-use"
CAPTCHA_MODEL = "claude-haiku-4-5-20251001"

_SCRAPER_JS = """
(function() {
  const mainTable = document.querySelector('table.table-sm tbody');
  if (!mainTable) return JSON.stringify([]);
  const rows = Array.from(mainTable.children);
  const results = [];
  let currentDomain = '';
  rows.forEach(row => {
    const innerTable = row.querySelector('table');
    const domainLink = row.querySelector('a');
    if (!innerTable && domainLink) {
      currentDomain = domainLink.textContent.trim();
    } else if (innerTable) {
      const allTr = innerTable.querySelectorAll('tr');
      for (let i = 1; i < allTr.length; i++) {
        const cells = Array.from(allTr[i].querySelectorAll('td, th'));
        if (cells.length >= 5) {
          const attrLink = cells[1].querySelector('a');
          results.push({
            domain: currentDomain,
            attributeValue: attrLink ? attrLink.textContent.trim() : cells[1].textContent.trim(),
            firstDetected: cells[2].textContent.trim(),
            lastDetected: cells[3].textContent.trim(),
            overlapDuration: cells[4].textContent.trim()
          });
        }
      }
    }
  });
  return JSON.stringify(results);
})()
"""


def _run(args: list[str], timeout: int = 60) -> str:
    env = {**os.environ, "IN_DOCKER": "true", "DISPLAY": ":99"}
    result = subprocess.run(
        [BROWSER_USE, "--headed"] + args,
        capture_output=True, text=True, env=env, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"browser-use {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _has_captcha() -> bool:
    out = _run(["eval", "document.querySelector('#human-test-img') ? '1' : '0'"])
    return out.strip().endswith("1")


def _captcha_word() -> str:
    text = _run(["eval", "document.body.innerText"])
    capture_next = False
    for line in text.splitlines():
        line = line.strip()
        if "Select both" in line:
            capture_next = True
            continue
        if capture_next and line:
            return line.split()[-1].lower()
    raise RuntimeError("Could not extract captcha word from page")


def _solve_captcha() -> None:
    try:
        _run(["wait", "selector", "#human-test-img, table.table-sm", "--timeout", "5000"])
    except Exception:
        pass

    if not _has_captcha():
        return

    log.info("[builtwith] Captcha detected, solving...")

    state = _run(["state"])
    img_index = None
    for line in state.splitlines():
        if "human-test-img" in line:
            img_index = line.strip().lstrip("[").split("]")[0]
            break
    if not img_index:
        raise RuntimeError("Could not find captcha image index in page state")

    bbox_out = _run(["get", "bbox", img_index])
    bbox_line = next(l for l in bbox_out.splitlines() if l.startswith("bbox:"))
    bbox: dict = eval(bbox_line.replace("bbox: ", ""))  # noqa: S307

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        shot_path = f.name
    _run(["screenshot", shot_path])

    # Crop to just the captcha grid so Claude gets a clean 4x3 grid image
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        crop_path = f.name
    img = Image.open(shot_path)
    os.unlink(shot_path)
    cropped = img.crop((
        int(bbox["x"]),
        int(bbox["y"]),
        int(bbox["x"] + bbox["width"]),
        int(bbox["y"] + bbox["height"]),
    ))
    cropped.save(crop_path)

    word = _captcha_word()
    log.info("[builtwith] Captcha word: %s", word)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    with open(crop_path, "rb") as f:
        img_b64 = base64.standard_b64encode(f.read()).decode()
    os.unlink(crop_path)

    resp = client.messages.create(
        model=CAPTCHA_MODEL,
        max_tokens=100,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                },
                {
                    "type": "text",
                    "text": (
                        f'This image shows ONLY a 4-column x 3-row grid of 12 photos (4 wide, 3 tall).\n'
                        f'The word to match is: "{word}".\n'
                        f'Find the TWO photos that show a "{word}".\n'
                        f'Reply ONLY with JSON, no explanation: '
                        f'[{{"row": 0, "col": 0}}, {{"row": 1, "col": 2}}] '
                        f'(zero-indexed, row 0 = top row, col 0 = leftmost column)'
                    ),
                },
            ],
        }],
    )

    raw_text = resp.content[0].text.strip()
    log.info("[builtwith] Haiku response: %s", raw_text)
    json_match = re.search(r"\[.*?\]", raw_text, re.DOTALL)
    if not json_match:
        raise RuntimeError(f"Could not parse captcha coords from: {raw_text!r}")
    coords = json.loads(json_match.group(0))

    cell_w = bbox["width"] / 4
    cell_h = bbox["height"] / 3

    for c in coords[:2]:
        x = int(bbox["x"] + (c["col"] + 0.5) * cell_w)
        y = int(bbox["y"] + (c["row"] + 0.5) * cell_h)
        _run(["click", str(x), str(y)])
        log.info("[builtwith] Clicked captcha cell row=%d col=%d at (%d,%d)",
                 c["row"], c["col"], x, y)

    try:
        _run(["wait", "selector", "table.table-sm", "--timeout", "10000"])
    except Exception:
        pass


def scrape_relationships(domain: str) -> list[dict[str, Any]]:
    """Return BuiltWith relationship rows for domain. Empty list = no data."""
    url = f"https://builtwith.com/relationships/{domain}"
    log.info("[builtwith] Opening %s", url)

    _run(["open", url], timeout=30)
    _solve_captcha()

    raw = _run(["eval", _SCRAPER_JS])
    if raw.startswith("result: "):
        raw = raw[len("result: "):]

    rows: list[dict] = json.loads(raw)
    log.info("[builtwith] %s -> %d relationship rows", domain, len(rows))
    return rows
