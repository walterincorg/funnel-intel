-- Funnel session recordings — scripted replay of browser-use traversals.
-- Captured on the first successful run per competitor and reused for subsequent
-- scans via backend/worker/replay.py. Single LLM patches mutate action_log in
-- place; the canonical trace.zip stays frozen until a full re-record is needed.
CREATE TABLE IF NOT EXISTS funnel_recordings (
  competitor_id uuid PRIMARY KEY REFERENCES competitors(id) ON DELETE CASCADE,
  trace_path text,
  action_log jsonb NOT NULL DEFAULT '[]'::jsonb,
  captured_at timestamptz NOT NULL DEFAULT now(),
  patch_count int NOT NULL DEFAULT 0,
  is_stale boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_funnel_recordings_stale
  ON funnel_recordings(is_stale) WHERE is_stale = true;

-- Supabase Storage bucket for trace.zip artifacts. Private bucket — only the
-- service-role key (used by the worker + backend) can read/write.
INSERT INTO storage.buckets (id, name, public)
VALUES ('funnel-recordings', 'funnel-recordings', false)
ON CONFLICT (id) DO NOTHING;
