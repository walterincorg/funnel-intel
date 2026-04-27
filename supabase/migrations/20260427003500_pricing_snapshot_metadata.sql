ALTER TABLE pricing_snapshots
  ADD COLUMN IF NOT EXISTS metadata jsonb;
