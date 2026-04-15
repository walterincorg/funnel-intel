-- Ship list tables — the product surface the synthesis layer writes to.
--
-- ship_list_items: the weekly 0-5 actionable recommendations a DTC operator
-- receives every Monday. Each item cites one or more patterns (strict
-- citation, validated before persist) and carries a test plan the operator
-- can execute that week.
--
-- ship_list_outcomes: the feedback loop. After an operator ships an item
-- and 14 days elapse, we ask them "did it work" and record the result.
-- Future synthesis runs use prior outcomes to weight confidence for
-- similar patterns.

CREATE TABLE IF NOT EXISTS ship_list_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  week_of date NOT NULL,
  rank int NOT NULL,
  headline text NOT NULL,
  recommendation text NOT NULL,
  test_plan text NOT NULL,
  effort_estimate text NOT NULL CHECK (effort_estimate IN ('XS', 'S', 'M', 'L')),
  confidence real NOT NULL CHECK (confidence >= 0 AND confidence <= 10),
  pattern_ids uuid[] NOT NULL,
  swipe_file_refs jsonb,          -- [{type:'ad'|'scan_step', id:uuid, label?:text}]
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN (
    'proposed', 'shipping', 'shipped', 'skipped', 'expired'
  )),
  shipped_at timestamptz,
  generated_by_run_id uuid,        -- synthesis_runs.id (nullable, foreign key added in STEP 8)
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (week_of, rank)
);

CREATE INDEX IF NOT EXISTS idx_ship_list_items_week
  ON ship_list_items(week_of DESC);

CREATE INDEX IF NOT EXISTS idx_ship_list_items_status
  ON ship_list_items(status);

CREATE INDEX IF NOT EXISTS idx_ship_list_items_patterns
  ON ship_list_items USING gin(pattern_ids);

-- Outcome tracking for the feedback loop.
CREATE TABLE IF NOT EXISTS ship_list_outcomes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ship_list_item_id uuid NOT NULL REFERENCES ship_list_items(id) ON DELETE CASCADE,
  outcome text NOT NULL CHECK (outcome IN ('won', 'lost', 'inconclusive', 'not_tested')),
  notes text,
  recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ship_list_outcomes_item
  ON ship_list_outcomes(ship_list_item_id);
