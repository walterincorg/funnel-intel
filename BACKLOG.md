# Backlog

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
