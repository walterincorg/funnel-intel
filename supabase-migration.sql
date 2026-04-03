-- ⚠️ DESTRUCTIVE: Drops all existing tables and recreates with new schema
-- Backup saved at: /opt/funnel-intel/backup-20260403-044407

-- Drop existing tables (order matters due to foreign keys)
DROP TABLE IF EXISTS change_events CASCADE;
DROP TABLE IF EXISTS alert_routes CASCADE;
DROP TABLE IF EXISTS ad_observations CASCADE;
DROP TABLE IF EXISTS ads CASCADE;
DROP TABLE IF EXISTS funnel_steps CASCADE;
DROP TABLE IF EXISTS snapshots CASCADE;
DROP TABLE IF EXISTS scan_jobs CASCADE;
DROP TABLE IF EXISTS scan_runs CASCADE;
DROP TABLE IF EXISTS competitors CASCADE;
DROP TABLE IF EXISTS pricing_snapshots CASCADE;
DROP TABLE IF EXISTS scan_steps CASCADE;

-- === COMPETITORS ===
CREATE TABLE competitors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  slug text UNIQUE NOT NULL,
  funnel_url text NOT NULL,
  config jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- === SCAN RUNS ===
CREATE TABLE scan_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'pending',
  started_at timestamptz,
  completed_at timestamptz,
  total_steps int,
  stop_reason text,
  summary jsonb,
  is_baseline boolean DEFAULT false,
  baseline_run_id uuid REFERENCES scan_runs(id),
  drift_level text,
  drift_details jsonb,
  progress_log jsonb DEFAULT '[]'::jsonb,
  created_at timestamptz DEFAULT now()
);

-- === SCAN STEPS ===
CREATE TABLE scan_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
  step_number int NOT NULL,
  step_type text NOT NULL,
  question_text text,
  answer_options jsonb,
  action_taken text,
  url text,
  screenshot_path text,
  metadata jsonb,
  created_at timestamptz DEFAULT now()
);

-- === PRICING SNAPSHOTS ===
CREATE TABLE pricing_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  plans jsonb,
  discounts jsonb,
  trial_info jsonb,
  captured_at_step int,
  url text,
  screenshot_path text,
  created_at timestamptz DEFAULT now()
);

-- === SCAN JOBS (queue) ===
CREATE TABLE scan_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  status text DEFAULT 'pending',
  priority int DEFAULT 0,
  picked_at timestamptz,
  created_at timestamptz DEFAULT now()
);

-- === INDEXES ===
CREATE INDEX idx_scan_runs_competitor ON scan_runs(competitor_id);
CREATE INDEX idx_scan_runs_status ON scan_runs(status);
CREATE INDEX idx_scan_runs_baseline ON scan_runs(competitor_id) WHERE is_baseline = true;
CREATE INDEX idx_scan_steps_run ON scan_steps(run_id);
CREATE INDEX idx_scan_steps_run_number ON scan_steps(run_id, step_number);
CREATE INDEX idx_pricing_competitor ON pricing_snapshots(competitor_id);
CREATE INDEX idx_scan_jobs_pending ON scan_jobs(status, priority DESC, created_at);
