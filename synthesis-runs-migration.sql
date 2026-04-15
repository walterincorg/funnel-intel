-- Synthesis run observability — mirrors ad_scrape_runs / domain_intel_runs.
--
-- Every invocation of the weekly synthesis pipeline (pattern extraction +
-- ship list generation) inserts a row here. Status transitions:
--   pending      : manual trigger waiting to run
--   running      : currently executing (also the recovery target on worker
--                  restart: any 'running' row from a dead worker is marked
--                  'failed' by cleanup_stale_jobs)
--   completed    : pipeline finished, ship list may be empty but it's honest
--   empty        : pipeline ran but produced zero items (no signal this week)
--   aborted_stale: freshness gate rejected the run — one or more sources
--                  beyond FRESHNESS_STALE_HOURS
--   failed       : unrecoverable error

CREATE TABLE IF NOT EXISTS synthesis_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN (
    'pending', 'running', 'completed', 'empty', 'aborted_stale', 'failed'
  )),
  week_of date NOT NULL,
  trigger text NOT NULL DEFAULT 'scheduled' CHECK (trigger IN ('scheduled', 'manual')),

  -- Inputs observed
  candidate_pattern_count int,
  prior_outcome_count int,
  stale_sources jsonb,   -- list of {source, competitor_id, last_success_at, last_failure_at, last_error}

  -- Outputs
  patterns_found int,
  patterns_persisted int,
  ship_list_item_count int,
  items_rejected_shape int,
  items_rejected_citation int,
  retries int,

  -- Cost
  llm_cost_cents int,
  input_tokens int,
  output_tokens int,

  -- Timing
  started_at timestamptz,
  completed_at timestamptz,
  duration_s int,

  error text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_synthesis_runs_status
  ON synthesis_runs(status);

CREATE INDEX IF NOT EXISTS idx_synthesis_runs_week
  ON synthesis_runs(week_of DESC);

CREATE INDEX IF NOT EXISTS idx_synthesis_runs_created_at
  ON synthesis_runs(created_at DESC);
