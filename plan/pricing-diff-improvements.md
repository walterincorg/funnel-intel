# Pricing Diff Improvements

Two related improvements for how pricing changes are detected and alerted on.
Do these together in one branch — they're conceptually linked.

---

## 1. Rolling diff (compare against previous run, not first baseline)

### Problem

Right now `differ.py` always anchors pricing comparison to the **first baseline run**:

```python
baseline_pricing_res = db.table("pricing_snapshots") \
    .select("*") \
    .eq("run_id", baseline_run["id"])  # first run that was promoted
    .limit(1).execute()
```

If a competitor raises their price at scan 10, we alert correctly. But at scan 11,
12, 13... we keep comparing against the original price and keep alerting on the
**same price change forever**. The new price has become the new normal — we're
generating noise, not signal.

### Fix

Compare each new run's pricing against the **immediately preceding completed run**
for that competitor, not the original baseline.

```python
# Instead of fetching the baseline run's pricing_snapshot,
# fetch the most recent completed run before the current one.
prev_run_res = db.table("scan_runs") \
    .select("id") \
    .eq("competitor_id", competitor_id) \
    .eq("status", "completed") \
    .neq("id", current_run_id) \
    .order("completed_at", desc=True) \
    .limit(1) \
    .execute()

prev_pricing_res = db.table("pricing_snapshots") \
    .select("*") \
    .eq("run_id", prev_run_res.data[0]["id"]) \
    .limit(1).execute()
```

This way:
- Alert fires when price changes (run N vs run N-1)
- Alert does NOT repeat on run N+1, N+2 if price is stable at the new level
- Each alert represents a genuine new change

### Edge cases to handle

- **First run ever** — no previous run exists. Skip diff, or treat as new baseline, no alert.
- **Previous run had no pricing data** — fall back to the run before that, or skip diff.
- **Previous run failed** — skip failed runs when looking for the previous pricing snapshot.

---

## 2. Manual baseline promotion

### Problem

Baseline is auto-promoted on the first run that completes with ≥3 steps. After that,
there's no way to update it. If the funnel changes significantly (new questions, new
flow order, complete redesign), the baseline becomes stale — step diffs will always
show "major drift" even when the new flow is perfectly healthy.

### Fix

Add a "Promote to baseline" action on any completed run. This sets that run as the
new reference point for step drift comparison.

**What "baseline" controls:**
- Step-level diff: the guided replay prompt uses baseline steps as the script to follow
- Step drift detection: "none / minor / major" is relative to the baseline step sequence

**What baseline does NOT need to control (after improvement #1):**
- Pricing comparison — that becomes rolling (previous run), so baseline is irrelevant for pricing

### API

```
POST /api/competitors/{id}/runs/{run_id}/promote-baseline
```

Sets `scan_runs.is_baseline = true` for the given run, clears it for the previous baseline.

Or simpler: add a `baseline_run_id` column to `competitors` table and update it.
Either works — the second is cleaner (one place to look, no scanning scan_runs for
the `is_baseline` flag).

### UI

On the run history list / run detail view, add a "Set as baseline" button next to
each completed run. Grayed out if it's already the baseline.

Show which run is currently the baseline (e.g. a "Baseline" badge on the run card).

### When to use it

- After a competitor redesigns their funnel (new flow, not just copy changes)
- After manually verifying a run looks correct and want future diffs to reference it
- After price normalization: if a price stayed high for 3 months and you want to
  stop comparing against the old lower price even for step-level baseline

---

## Implementation order

Do rolling diff first — it's purely backend, lower risk, immediate noise reduction.

Baseline promotion touches UI + a DB schema change, so slightly more surface area.
But they can ship in the same branch.
