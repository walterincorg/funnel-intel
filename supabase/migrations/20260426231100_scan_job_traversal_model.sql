ALTER TABLE scan_jobs
  ADD COLUMN IF NOT EXISTS traversal_model text NOT NULL DEFAULT 'gpt-5.4-mini';

ALTER TABLE scan_jobs
  DROP CONSTRAINT IF EXISTS scan_jobs_traversal_model_check;
