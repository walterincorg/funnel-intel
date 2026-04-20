-- Stagehand traversal recipes: deterministic replay scripts per competitor.
--
-- The first successful AI-driven scan of a competitor funnel records an
-- ordered list of Stagehand `observe()` results. Subsequent scans replay
-- those observe results directly via `page.act()` without an autonomous
-- LLM agent. If a step's selector breaks, the driver self-heals by
-- re-observing, patches the step, and writes a new recipe row with
-- version = old.version + 1 (old row flipped to is_active = false).
--
-- One active recipe per competitor at a time; history is preserved.

CREATE TABLE IF NOT EXISTS traversal_recipes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    version int NOT NULL DEFAULT 1,
    start_url text NOT NULL,
    steps jsonb NOT NULL,
    stop_reason text,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    recorded_run_id uuid REFERENCES scan_runs(id) ON DELETE SET NULL,
    is_active boolean NOT NULL DEFAULT true,
    invalidated_reason text,
    invalidated_at timestamptz
);

-- Exactly one active recipe per competitor. The unique index is partial so
-- invalidated rows (is_active=false) don't conflict.
CREATE UNIQUE INDEX IF NOT EXISTS idx_traversal_recipes_active
    ON traversal_recipes(competitor_id) WHERE is_active;

CREATE INDEX IF NOT EXISTS idx_traversal_recipes_competitor
    ON traversal_recipes(competitor_id);

CREATE INDEX IF NOT EXISTS idx_traversal_recipes_recorded_run
    ON traversal_recipes(recorded_run_id);
