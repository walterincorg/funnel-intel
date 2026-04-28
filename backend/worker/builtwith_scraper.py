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
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import anthropic
from PIL import Image

from backend.config import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)
_DEBUG_LOG_PATH = Path("/Users/lukaspostulka/local browser use setup/.cursor/debug-8d43ee.log")


def _dbg(hypothesis_id: str, location: str, message: str, data: dict, run_id: str) -> None:
    payload = {
        "sessionId": "8d43ee",
        "id": f"log_{int(time.time() * 1000)}_{uuid4().hex[:8]}",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _resolve_browser_use() -> str:
    """Resolve the browser-use CLI in the active venv (cross-platform)."""
    override = os.getenv("BROWSER_USE_BIN")
    if override:
        return override
    bin_dir = Path(sys.executable).parent
    exe = "browser-use.exe" if sys.platform == "win32" else "browser-use"
    return str(bin_dir / exe)


BROWSER_USE = _resolve_browser_use()
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


def _run(args: list[str], timeout: int = 15) -> str:
    env = os.environ.copy()
    # VPS runs browser-use against an Xvfb display on Linux. On macOS / Windows
    # the OS provides a real display, so forcing DISPLAY=:99 makes Chromium
    # attach to a non-existent X server and the subprocess hangs.
    if sys.platform == "linux":
        env.setdefault("DISPLAY", ":99")
        env.setdefault("IN_DOCKER", "true")
    cmd_label = args[0]
    # Compact preview of args (truncate long JS/URLs)
    preview = " ".join(a if len(a) < 60 else a[:57] + "..." for a in args[1:])
    log.debug("[builtwith] _run start: %s %s (timeout=%ds)", cmd_label, preview, timeout)
    start = time.perf_counter()
    try:
        result = subprocess.run(
            [BROWSER_USE, "--headed"] + args,
            capture_output=True, text=True, env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start
        log.warning("[builtwith] _run TIMEOUT: %s after %.1fs (limit=%ds)",
                    cmd_label, elapsed, timeout,
                    extra={"duration_ms": round(elapsed * 1000)})
        raise
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        log.warning("[builtwith] _run FAIL: %s returncode=%d after %.1fs stderr=%s",
                    cmd_label, result.returncode, elapsed, result.stderr.strip()[:200],
                    extra={"duration_ms": round(elapsed * 1000)})
        raise RuntimeError(f"browser-use {cmd_label} failed: {result.stderr.strip()}")
    stdout = result.stdout.strip()
    log.debug("[builtwith] _run done:  %s took %.1fs (stdout=%d bytes)",
              cmd_label, elapsed, len(stdout),
              extra={"duration_ms": round(elapsed * 1000)})
    return stdout


def _has_captcha() -> bool:
    # browser-use's CLI has a 60s internal socket recv timeout. On a freshly
    # navigated page the first eval often trips it. Retry once on failure.
    js = "document.querySelector('#human-test-img') ? '1' : '0'"
    for attempt in (1, 2):
        try:
            out = _run(["eval", js], timeout=90)
            return out.strip().endswith("1")
        except (subprocess.TimeoutExpired, RuntimeError) as e:
            if attempt == 2:
                log.warning("[builtwith] _has_captcha failed after 2 tries (%s) — assuming no captcha", e)
                return False
            log.debug("[builtwith] _has_captcha attempt 1 failed (%s), retrying after 3s", type(e).__name__)
            time.sleep(3)
    return False


def _captcha_word() -> str:
    text = _run(["eval", "document.body.innerText"], timeout=30)
    capture_next = False
    for line in text.splitlines():
        line = line.strip()
        if "Select both" in line:
            capture_next = True
            continue
        if capture_next and line:
            return line.split()[-1].lower()
    raise RuntimeError("Could not extract captcha word from page")


def _attempt_captcha_solve(attempt: int) -> None:
    """One round of captcha solving. Raises if something unrecoverable happens."""
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
    log.info("[builtwith] Captcha attempt %d: word=%s", attempt, word)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    with open(crop_path, "rb") as f:
        img_b64 = base64.standard_b64encode(f.read()).decode()
    os.unlink(crop_path)

    haiku_start = time.perf_counter()
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

    haiku_elapsed = time.perf_counter() - haiku_start
    raw_text = resp.content[0].text.strip()
    log.info("[builtwith] Haiku responded in %.1fs: %s",
             haiku_elapsed, raw_text,
             extra={"duration_ms": round(haiku_elapsed * 1000)})
    all_matches = re.findall(r"\[.*?\]", raw_text, re.DOTALL)
    if not all_matches:
        raise RuntimeError(f"Could not parse captcha coords from: {raw_text!r}")
    coords = json.loads(all_matches[-1])

    cell_w = bbox["width"] / 4
    cell_h = bbox["height"] / 3

    for c in coords[:2]:
        x = int(bbox["x"] + (c["col"] + 0.5) * cell_w)
        y = int(bbox["y"] + (c["row"] + 0.5) * cell_h)
        _run(["click", str(x), str(y)])
        log.info("[builtwith] Clicked captcha cell row=%d col=%d at (%d,%d)",
                 c["row"], c["col"], x, y)

    # Let the page re-render so the next _has_captcha sees fresh state.
    time.sleep(2)


def _solve_captcha(max_attempts: int = 3, run_id: str = "pre-run") -> bool:
    captcha_start = time.perf_counter()
    if not _has_captcha():
        log.info("[builtwith] No captcha (check took %.1fs)",
                 time.perf_counter() - captcha_start)
        # region agent log
        _dbg(
            "H6",
            "backend/worker/builtwith_scraper.py:_solve_captcha",
            "Captcha not present",
            {"max_attempts": max_attempts},
            run_id=run_id,
        )
        # endregion
        return True

    log.info("[builtwith] Captcha detected, solving...")
    # region agent log
    _dbg(
        "H6",
        "backend/worker/builtwith_scraper.py:_solve_captcha",
        "Captcha detected",
        {"max_attempts": max_attempts},
        run_id=run_id,
    )
    # endregion
    for attempt in range(1, max_attempts + 1):
        try:
            _attempt_captcha_solve(attempt)
        except Exception:
            log.exception("[builtwith] Captcha attempt %d raised", attempt)
            if attempt == max_attempts:
                raise
            continue

        # Verify success — BuiltWith removes the captcha img when it's solved.
        if not _has_captcha():
            log.info("[builtwith] Captcha verified solved on attempt %d (%.1fs total)",
                     attempt, time.perf_counter() - captcha_start,
                     extra={"duration_ms": round((time.perf_counter() - captcha_start) * 1000)})
            # region agent log
            _dbg(
                "H6",
                "backend/worker/builtwith_scraper.py:_solve_captcha",
                "Captcha solved",
                {"attempt": attempt},
                run_id=run_id,
            )
            # endregion
            return True
        log.warning("[builtwith] Captcha still present after attempt %d — retrying", attempt)

    # Out of attempts. Don't raise — let the caller see the empty-result page
    # and move on. Losing one domain is better than failing the whole run.
    log.warning("[builtwith] Gave up on captcha after %d attempts (%.1fs)",
                max_attempts, time.perf_counter() - captcha_start)
    # region agent log
    _dbg(
        "H6",
        "backend/worker/builtwith_scraper.py:_solve_captcha",
        "Captcha unresolved after max attempts",
        {"max_attempts": max_attempts},
        run_id=run_id,
    )
    # endregion
    return False


def scrape_relationships(domain: str) -> list[dict[str, Any]]:
    """Return BuiltWith relationship rows for domain. Empty list = no data."""
    url = f"https://builtwith.com/relationships/{domain}"
    run_id = f"builtwith:{domain}"
    total_start = time.perf_counter()
    log.info("[builtwith] Opening %s", url)

    open_start = time.perf_counter()
    try:
        _run(["open", url], timeout=90)
    except Exception as e:
        # region agent log
        _dbg(
            "H7",
            "backend/worker/builtwith_scraper.py:scrape_relationships",
            "Open failed",
            {"domain": domain, "error": str(e)[:300]},
            run_id=run_id,
        )
        # endregion
        raise
    # Let the page settle — browser-use's CLI has a 60s socket recv timeout
    # internally, and the first eval after navigation frequently trips it
    # because the daemon is still busy processing async loads.
    time.sleep(4)
    open_elapsed = time.perf_counter() - open_start
    log.info("[builtwith] %s: open phase took %.1fs (incl. 4s settle)", domain, open_elapsed,
             extra={"duration_ms": round(open_elapsed * 1000)})

    captcha_phase_start = time.perf_counter()
    captcha_solved = _solve_captcha(run_id=run_id)
    captcha_elapsed = time.perf_counter() - captcha_phase_start
    log.info("[builtwith] %s: captcha phase took %.1fs", domain, captcha_elapsed,
             extra={"duration_ms": round(captcha_elapsed * 1000)})

    eval_start = time.perf_counter()
    # Retry once on failure — same socket-timeout flakiness as _has_captcha.
    try:
        raw = _run(["eval", _SCRAPER_JS], timeout=90)
    except (subprocess.TimeoutExpired, RuntimeError) as e:
        log.debug("[builtwith] scraper eval attempt 1 failed (%s), retrying", type(e).__name__)
        time.sleep(3)
        raw = _run(["eval", _SCRAPER_JS], timeout=90)
    eval_elapsed = time.perf_counter() - eval_start
    log.info("[builtwith] %s: eval phase took %.1fs (raw=%d bytes)",
             domain, eval_elapsed, len(raw),
             extra={"duration_ms": round(eval_elapsed * 1000)})

    if raw.startswith("result: "):
        raw = raw[len("result: "):]

    rows: list[dict] = json.loads(raw)

    # Debug: when we got 0 rows, capture what the page actually shows so we can
    # tell "legitimately empty" apart from "we hit a captcha/error page".
    if not rows:
        try:
            probe_js = (
                "JSON.stringify({"
                "title: document.title,"
                "h1: (document.querySelector('h1')||{}).innerText || null,"
                "hasCaptcha: !!document.querySelector('#human-test-img'),"
                "hasResultsTable: !!document.querySelector('table.table-sm'),"
                "hasNoResults: /no results|no relationships|not found/i.test(document.body.innerText),"
                "bodyPreview: document.body.innerText.slice(0, 400)"
                "})"
            )
            probe = _run(["eval", probe_js], timeout=20)
            if probe.startswith("result: "):
                probe = probe[len("result: "):]
            log.info("[builtwith] %s: empty-result page state: %s", domain, probe)
            # region agent log
            _dbg(
                "H8",
                "backend/worker/builtwith_scraper.py:scrape_relationships",
                "Empty rows with page probe",
                {"domain": domain, "captcha_solved": captcha_solved, "probe": json.loads(probe)},
                run_id=run_id,
            )
            # endregion
        except Exception as e:
            log.debug("[builtwith] %s: empty-result probe failed: %s", domain, e)
            # region agent log
            _dbg(
                "H8",
                "backend/worker/builtwith_scraper.py:scrape_relationships",
                "Empty rows and probe failed",
                {"domain": domain, "captcha_solved": captcha_solved, "probe_error": str(e)[:300]},
                run_id=run_id,
            )
            # endregion
    total_elapsed = time.perf_counter() - total_start
    log.info("[builtwith] %s -> %d relationship rows (total %.1fs: open=%.1fs captcha=%.1fs eval=%.1fs)",
             domain, len(rows), total_elapsed, open_elapsed, captcha_elapsed, eval_elapsed,
             extra={"duration_ms": round(total_elapsed * 1000)})
    return rows
