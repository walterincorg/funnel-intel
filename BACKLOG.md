# Backlog

## Ad Tracking: Verify 200-Ad Cap Actually Returns Top Ads

We cap at `limitPerSource: 200` per competitor. The actor's input schema says `scrapePageAds.sortBy` defaults to `impressions_desc`. But we tested this and **cannot confirm the sort is working**:

**What we know:**
- `limitPerSource: 10` returned 30 ads (overshoots ~3x, not "up to 30" as docs claim)
- The returned ads are NOT sorted by start_date, suggesting some other sort is applied
- `eu_total_reach` / impression data is **null in the response**, so we can't verify the sort order
- We're trusting the actor to sort by impressions before truncating, but have no proof

**What we need to verify:**
1. Run one competitor (e.g. Bioma Health) without `limitPerSource` to get ALL ads
2. Run the same competitor with `limitPerSource: 200`
3. Compare: are the capped 200 a subset of the top 200 by longevity/impressions? Or are long-running proven winners (30d+) missing from the capped set?
4. Check if the 3x overshoot is consistent (does `limitPerSource: 200` return ~600?)

**Risk:** If the sort doesn't work, we're getting an arbitrary 200-600 ads instead of the most important ones. The LLM analysis quality depends on having the right ads.

**Cost of verification:** One uncapped run for a large competitor costs ~$0.50. Worth it to validate the whole pipeline.

## Nice to Have: Settings Page

A UI page to configure worker and scrape behaviour without touching env vars or code.

**Fields to expose:**
- Scan timeout (currently hardcoded `SCAN_TIMEOUT = 45min` in `loop.py`)
- Max steps per scan (currently `max_steps: 100` in `strategies.py`)
- Ad scrape hour UTC (currently `AD_SCRAPE_HOUR_UTC = 6` env var)
- Apify max results cap per competitor (currently no cap — see Apify cost backlog item)
- Apify country filter (currently no filter)
- Poll interval (currently `POLL_INTERVAL = 10s`)

**Approach:** Store settings in a `settings` table in Supabase (key/value or single-row JSON). Worker reads on startup (or per-job). Frontend has a Settings page with form inputs.

## Gate Detection & Skip System

When the agent encounters something it can't complete, it currently tries to hack around it (e.g. fetch interceptors for palm scans), wasting steps and sometimes crashing the funnel.

**Problem:** Media/biometric gates, CAPTCHAs, OAuth, SMS codes, interactive widgets (sliders, canvas drawing) are all things the bot can't genuinely complete.

**Proposed solution:**
- Detect the gate type (media_upload, captcha, sms_verification, oauth, interactive_widget)
- Log it as a step with `step_type: "gate"` describing what's required
- Try to find a "Skip" / "Not now" button
- If no skip exists, stop cleanly with `stop_reason: "gate_blocked"`

**Why it matters:** Knowing where competitors place gates is competitive intelligence. Clean detection is better than failed workarounds.

**Discovered in:** Nebula palm scan gate — agent wasted ~10 steps trying to bypass it, eventually crashed the funnel.

## Ad Tracking: Fix `failed_test` Signal + Unreliable Fields

The `curious_coder/facebook-ads-library-scraper` Apify actor has known data issues that affect signal accuracy.

**`stop_date` is fake for active ads:** The actor defaults `stop_date` to the current date instead of null for active ads. 80% of our first scrape has `stop_date = scrape date`. This is a [known issue](https://apify.com/curious_coder/facebook-ads-library-scraper/issues/end-date-not-null-ev-4joYVfqbtfym7xXGn).

**`platforms` field is always empty:** The actor doesn't return `publisher_platforms`, so `platform_expansion` signals will never fire.

**What to fix:**
- `failed_test` signal: Change from checking `stop_date`/status to **disappearance-based detection** — ad was in yesterday's scrape but absent from today's, and `start_date` was within 7 days. This is the reliable way to detect killed ads.
- `platform_expansion` signal: Either find an Apify actor that returns platforms, or remove this signal.
- Consider ignoring `stop_date` in signal logic entirely — only trust `start_date` and presence/absence in daily scrapes.

**Priority:** Fix before second daily scrape (day 2 is when these signals would first fire).

## Ad Tracking: Creative URL Coverage (Missing image_url/video_url)

For Headway's top headline (1,000 ads), ~40% have neither `image_url` nor `video_url`. These are likely carousel ads or ad formats where our normalizer in `ad_scraper.py` (`normalize_ad()`) fails to extract the creative URL.

**Needs investigation:**
- What format are these ads? Check `raw_data` in `ad_snapshots` for a sample of null-creative ads
- Are `cards` being populated for carousels? Current normalizer only takes `cards[0]`
- Does the Apify response have the creative under a different field for these cases?

**Why it matters:** Creative URL is needed for visual change detection in future. Currently signals don't depend on it, but it's a gap.

## Ad Tracking: Apify Cost Concern

First partial scrape (only a few competitors) already used $12 of a $29 plan. At this rate daily scraping of all 15 competitors would blow through the budget in days.

**Findings from first scrape:**
- Pricing: $0.00075 per ad ("pay per event")
- First partial run (3-4 competitors): 25,288 ads = **$18.97** — nearly the entire $29 plan in one run
- Full 15-competitor daily run estimated at 50,000-100,000 ads = **$37-75/day = $1,100-2,250/month**
- Root cause: fetching every active ad including all language variants (same ad in Japanese, French, Portuguese, Spanish etc.)

**Immediate fixes needed before next scrape:**
1. **Cap results** — add `maxResults: 200` to Apify input in `ad_scraper.py` to only fetch top ads by impressions
2. **Filter by country** — add `country=US` to cut out localized variants (Headway alone had many non-English ads)
3. **Investigate per-run pricing actors** as an alternative to pay-per-event

**Do NOT run another full scrape until this is fixed.**

## Ad Tracking: Batch DB Inserts (Performance)

Currently the ad ingestion loop does 4 individual Supabase round-trips per ad (2x SELECT + 2x INSERT/UPDATE). For large advertisers like Headway (1,766 ads) this means ~7,000 queries and takes 6+ minutes just for one competitor.

**Fix:**
- Fetch all existing `meta_ad_id`s for the competitor in a single query upfront
- Use Supabase batch insert for `ads` and `ad_snapshots`
- Use upsert with `on_conflict` instead of SELECT-then-INSERT pattern

**Expected improvement:** ~10x faster ingestion, scraping all 15 competitors in minutes instead of an hour.

## Ad Tracking: Apify Timeout on Slow Competitors

TodayIsTheDay failed on first scrape with `TimeoutError: The read operation timed out`. The current Apify sync timeout is 300s — some competitors (especially those using `view_all_page_id` URL format instead of keyword search) are slower and exceed this.

**Fix options:**
- Increase `SYNC_TIMEOUT` in `backend/worker/ad_scraper.py` (currently 300s) to 600s
- Switch from the sync endpoint (`run-sync-get-dataset-items`) to async (start run, poll for completion) to avoid HTTP-level timeouts
- Add retry logic (e.g. 2 attempts before giving up)

**Affected competitors:** The majority of our list uses `view_all_page_id` (page-based search), which is slower than keyword search because Meta looks up all ads from a specific Facebook page rather than doing a text index lookup. Affected: TodayIsTheDay, RISE SCIENCE, BetterMe (Mental Health, Men, Wall Pilates, Treadmill, Meal Plan), Nebula, Woofz, DR. SQUATCH, Happy Mammoth, Savvy Finds, MOERIE, Bioma Health.

Keyword-based search (`search_type=keyword_exact_phrase`) is faster but fragile — if a brand changes their domain the search breaks. Page ID is more reliable long-term but slower.

## Structured Logging System

Currently using basic `logging.getLogger()` with no structured output, no log aggregation, and no way to trace failures across the scrape pipeline. When something breaks (Apify timeout, DB insert failure, LLM analysis error), diagnosing requires SSH-ing into the VPS and grepping through unstructured log files.

**What we need:**
- Structured JSON logging so every log line includes context (competitor_id, scrape_run_id, step name, duration)
- Log levels used consistently: ERROR for failures that stop a competitor's scrape, WARNING for retries/skips, INFO for normal progress
- A way to trace a single scrape run end-to-end: which competitors succeeded, which failed, where they failed, and why
- Persistent log storage beyond the current systemd journal rotation

**Options to evaluate:**
- **Minimal:** Switch to `structlog` or `python-json-logger` for structured JSON output, ship logs to a file with rotation, add a `/api/logs` endpoint for recent errors
- **Managed:** Ship structured logs to a free-tier service (Betterstack, Axiom, or Grafana Cloud) for search, filtering, and alerting
- **Self-hosted:** Loki + Grafana on the VPS (heavier but no external dependency)

**Why it matters:** The ad scraping pipeline runs unattended on a schedule. When a competitor fails silently (like the TodayIsTheDay timeout), we don't know until someone manually checks. Structured logs with alerting on ERROR-level events would catch these immediately.

## Browser Use Cloud

Migrate from local browser-use to Browser Use Cloud. Currently the browser-use agent runs locally on the VPS, which ties up the single worker thread during scans and limits concurrency. Browser Use Cloud would offload browser automation to their managed infrastructure, freeing up the worker for other tasks.

**Benefits:**
- No local browser/Chromium dependency on the VPS
- Better concurrency — multiple scans can run in parallel
- More reliable — managed infrastructure handles crashes, timeouts, resource limits
- Scales without VPS resource constraints

**What to investigate:**
- Browser Use Cloud API and pricing
- How to swap the local `browser_use.Agent` calls in `traversal.py` for cloud API calls
- Whether the cloud version supports the same LLM-driven agent interface
- Impact on fingerprint extraction in `domain_intel.py` (currently uses HTTP, but browser fallback would use cloud)

## Test Coverage for Ad Pipeline

Zero test files exist in this project. The ad pipeline was rewritten (batch upserts, disappearance-based failed_test, LLM analysis) without tests. Priority test files:

1. **`test_ad_signals.py`** — Unit tests for `compute_signals()` with mocked DB. Cover: new_ad detection, proven_winner threshold (30d), disappearance-based failed_test (7d window), copy_change diff, count_spike math, deduplication of existing signals.
2. **`test_ad_analysis.py`** — Unit test for LLM JSON parsing and `meta_ad_id` → UUID mapping with mocked Anthropic response. Cover: valid response, malformed response, missing tool_use block, empty ad list.
3. **`test_ad_loop.py`** — Integration-style test verifying batch upsert produces correct `ad_id_map`, raw_data mapped to correct ad, new vs existing ad handling.

**Why it matters:** Signal logic drives customer-facing insights. A bug in failed_test or proven_winner silently produces wrong data. The LLM analysis pipeline is non-deterministic. Without tests, bugs are only caught when the customer notices wrong data.
