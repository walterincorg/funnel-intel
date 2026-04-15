# `ceo_impl` — 10x Roadmap Status

Branch: `ceo_impl` (from `main` at `861fd52`)
Last updated: 2026-04-15

Implementation of the weekly Ship List synthesis layer from the CEO review.
Seven commits, six new Supabase migrations, 196 pytest green, frontend
`tsc --noEmit` exit 0. All code is landed. What's left is operational:
apply migrations, set env vars, trigger the first real run, and tune.

---

## What shipped

| # | Item | Commit | Status |
|---|---|---|---|
| PREREQ 1 | Apify cost env-config + ingest hot-loop cleanup | `da92f94` | ✅ |
| PREREQ 2 | Batch upsert for ads + snapshots | (already existed) | ✅ |
| PREREQ 4 | `data_freshness` table + markers in every loop | `da92f94` | ✅ |
| PREREQ 5 | SimHash creative clustering | `31f9ec7` | ✅ |
| STEP 6 | Pattern extraction engine (6 detectors) | `626d722` | ✅ |
| STEP 7 | Ship list generator (LLM + citation validator) | `fa46c29` | ✅ |
| STEP 8 | Weekly synthesis loop + freshness gate | `938e083` | ✅ |
| STEP 9 | Ship list API + React document page | `8c0b79d` | ✅ |
| STEP 10 | 14-day outcome feedback loop | `223cb0d` | ✅ |

---

## Before this actually runs end-to-end

### 1. Apply the six migrations

All additive (CREATE IF NOT EXISTS or ALTER...ADD COLUMN IF NOT EXISTS).
Can run in any order. Apply via the Supabase SQL editor or `psql`:

```
freshness-migration.sql
creative-clusters-migration.sql
patterns-migration.sql
ship-list-migration.sql
synthesis-runs-migration.sql
feedback-loop-migration.sql
```

Until applied, every new code path catches the DB error and the worker
stays green, but nothing persists. The ship list page will show an empty
state and the worker will log "table does not exist" warnings.

### 2. Env vars

Required:
- `ANTHROPIC_API_KEY` — already used by `ad_analysis.py`, same var. The
  ship list generator fails fast with `LLMError("ANTHROPIC_API_KEY not
  configured")` if missing.

Optional (all have defaults):
- `SYNTHESIS_MODEL` (default `claude-sonnet-4-20250514`)
- `SYNTHESIS_COST_CAP_USD` (default `10.0`) — hard projected-cost ceiling
  per LLM call. Default is ~200x a normal run; only triggers on runaway
  prompt/max_tokens bugs.
- `SYNTHESIS_DAY_OF_WEEK` (default `0` = Monday)
- `SYNTHESIS_HOUR_UTC` (default `7`) — runs after the Monday 6am ad scrape
- `SYNTHESIS_MAX_FAILURES_PER_DAY` (default `3`)
- `FRESHNESS_STALE_HOURS` (default `48`) — synthesis aborts if any tracked
  source hasn't succeeded within this window
- `FEEDBACK_WAIT_DAYS` (default `14`) — how long after shipping before
  prompting for an outcome
- `AD_SCRAPE_LIMIT_PER_SOURCE` (default `200`)
- `AD_SCRAPE_COUNTRY_CODE` (default `US`)

### 3. Trigger the first run

Don't wait for Monday 7am UTC. Force it:

```
POST /api/ship-list/synthesis/trigger
```

The next worker poll (every 10s) picks up the pending row and runs the
full pipeline. Watch the worker log for the pipeline phases and open the
Ship List page (now the default `/` route) to see the output.

---

## Open follow-ups

### Near-term (day-sized each)

- **PREREQ 3 — ad pipeline test coverage.** Recorded Apify fixture at
  `tests/fixtures/apify_bioma_health.json` + unit tests for every signal
  type in `ad_signals.py`. Deferred during the 10x build because the
  synthesis layer catches its own DB errors, but the ad signal bugs from
  `BACKLOG.md` will propagate into pattern extraction if they ever
  regress. ~1 day with CC.

- **Apify sort verification.** `BACKLOG.md` lines 3-21: run one uncapped
  scrape per large competitor (Headway, BetterMe) to confirm the
  `limitPerSource: 200` cap is actually returning the top 200 by
  impressions and not random slices. ~1 hour + ~$5 Apify credits.

- **Golden sets for LLM eval paths.** Three prompts in the synthesis
  layer need regression tests:
  - `ad_analysis.py` → `tests/evals/golden/ad_analysis.jsonl`
  - `pattern_extraction` narrative pass (not yet built)
  - `ship_list.py` → `tests/evals/golden/ship_list.jsonl`
  Hand-craft 20 examples per path, add a CI job that re-runs the prompt
  on each change and fails if >2/20 diverge from baseline. ~1 day.

- **Prompt v2 iteration.** `backend/prompts/ship_list_v1.md` is v1. Expect
  to tune after seeing what Claude actually produces on real patterns.
  The versioned filename convention is in place: copy to `ship_list_v2.md`
  for the next iteration, bump `PROMPT_VERSION` in `ship_list.py`, and
  the `synthesis_runs` row tracks which prompt produced which list.

### Medium-term (feature extensions)

- **Pattern narratives.** `patterns.narrative` column exists but is
  unused. A second LLM pass in `pattern_extraction.py` could add one-
  sentence explanations to each pattern, which would make the Ship List
  prompt richer. Uses the shared `services/llm.py`. ~half day.

- **Freshness dashboard page.** The API (`GET /api/ship-list/freshness`)
  is built but there's no React surface for it. A simple table page
  showing each (source, competitor) row with `is_stale` flag, last
  success/failure timestamps, and last error. ~half day.

- **Synthesis runs observability page.** `GET /api/ship-list/synthesis-runs`
  returns the last 20 runs. A small page showing cost over time, item
  counts, retry rates, aborted_stale frequency would be a useful ops
  view. ~half day.

- **Manual trigger UI.** `POST /api/ship-list/synthesis/trigger` is wired
  but there's no button on the Ship List page for it. Add one to the
  empty state and the header. 15 minutes.

### Long-term (deferred from CEO review)

These were explicitly deferred to Phase 2 in the CEO review. Don't build
them until Phase 1 (the core synthesis → ship list → feedback loop) has
been validated with real data:

- **Confidence backtest.** After 3+ months of ship list outcomes, compute
  "stated-8/10 items actually won X% of the time" and publish it as a
  trust-building number.
- **Creative fingerprint matching / angle clustering.** SimHash already
  dedupes within a competitor. Cross-competitor angle clustering (via
  embeddings) would surface patterns across operators.
- **Funnel question library.** Every unique question from `scan_steps`,
  grouped semantically, browsable by step index.
- **Price ladder view.** Normalized pricing chart across competitors over
  time.
- **Ad longevity leaderboard.** "Ads running 60+, 90+, 180+ days across
  all competitors." Pure SQL view, ~1 hour.
- **"Launch imminent" predictor.** Combines new domain registrations +
  tracking setup + early ad trickle into a 30-90 day launch forecast.
- **Founder-funnel self-ingest.** Feed the operator's own funnel through
  the same scanner, diff against top competitors monthly.
- **Operator cluster force-directed graph.** Visualization of the
  existing domain_clustering data. Cosmetic, viral potential.
- **Swipe file PDF export.** One-click creative swipe file for a
  competitor. Marketing artifact.

### Explicitly NOT in scope

From the CEO review, ruled out as "right ideas, wrong product":
- SEO / SERP tracking
- Social signals (Twitter, LinkedIn)
- Press / PR / funding news
- Review site aggregation
- Customer sentiment NLP

---

## Architecture reminder

The worker loop runs five peers in one thread (see `backend/worker/loop.py`):

```
main() poll cycle:
  1. funnel scan jobs (on-demand via scan_jobs queue)
  2. maybe_run_ad_scrape       (daily, Mon+Thu 06:00 UTC)
  3. maybe_run_domain_intel    (weekly, Tue 07:00 UTC)
  4. maybe_run_synthesis       (weekly, Mon 07:00 UTC)  <- new
  5. maybe_run_feedback_check  (every poll, idempotent) <- new
```

The synthesis pipeline itself is four phases inside `_run_synthesis`:

```
freshness gate → pattern extraction → ship list LLM → persist observability
(refuse on stale)  (6 detectors)       (citation validator)  (synthesis_runs row)
```

The three hallucination guardrails on the ship list LLM are:
1. Anthropic tool_use (schema enforced)
2. `validate_item_shape` (defensive post-check)
3. `resolve_citations` (every `pattern_id` must exist in the DB)

One retry with correction note on zero-validated-items, then `status=failed`.

---

## Files of interest

New code:
- `backend/worker/freshness.py` (PREREQ 4)
- `backend/worker/creative_cluster.py` (PREREQ 5)
- `backend/worker/pattern_extraction.py` (STEP 6)
- `backend/services/llm.py` (STEP 7 — shared Anthropic wrapper)
- `backend/prompts/ship_list_v1.md` (STEP 7)
- `backend/worker/ship_list.py` (STEP 7)
- `backend/worker/synthesis_loop.py` (STEP 8)
- `backend/routers/ship_list.py` (STEP 9)
- `frontend/src/pages/ShipList.tsx` (STEP 9)
- `backend/worker/feedback_loop.py` (STEP 10)

New migrations (all must apply):
- `freshness-migration.sql`
- `creative-clusters-migration.sql`
- `patterns-migration.sql`
- `ship-list-migration.sql`
- `synthesis-runs-migration.sql`
- `feedback-loop-migration.sql`

New tests:
- `tests/test_creative_cluster.py` (37 tests)
- `tests/test_pattern_extraction.py` (36 tests)
- `tests/test_ship_list.py` (46 tests)
- `tests/test_synthesis_loop.py` (18 tests)
- `tests/test_feedback_loop.py` (22 tests)

Total: 196 tests green, all pure-logic, no DB or network required.

Test infra also added: `conftest.py` at repo root so `from backend.X` works
without PYTHONPATH.
