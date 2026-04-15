-- Patterns table — deterministic cross-source observations that feed the
-- weekly ship list generator.
--
-- A "pattern" is a high-signal observation mined from the raw data pipelines:
-- ads + creative clusters + scan runs + pricing snapshots + domain changes +
-- discovered domains. Each pattern cites the specific rows that justify it
-- via evidence_refs so the ship list LLM can be held to strict citation.
--
-- Dedup: every pattern carries a stable signature_hash derived from the
-- pattern type + its core identifying evidence. Re-running the extractor is
-- idempotent — a pre-existing signature bumps last_seen_at instead of
-- creating a duplicate row. This lets the synthesis loop run weekly without
-- drift, and lets confidence rise organically as the same pattern is
-- observed on successive runs.

CREATE TABLE IF NOT EXISTS patterns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pattern_type text NOT NULL CHECK (pattern_type IN (
    'funnel_change',
    'ad_angle_shift',
    'price_move',
    'launch_signal',
    'proven_winner',
    'killed_test'
  )),
  signature_hash text NOT NULL UNIQUE,
  observed_in_competitors uuid[] NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  observation_count int NOT NULL DEFAULT 1,
  confidence real NOT NULL CHECK (confidence >= 0 AND confidence <= 10),
  evidence_refs jsonb NOT NULL,  -- [{type:'ad'|'scan_run'|'cluster'|'pricing'|'domain_change'|'discovered_domain', id:uuid, label?:text}, ...]
  headline text,                  -- one-line machine summary ("BetterMe rotated cluster abc -> def")
  narrative text,                 -- optional LLM-generated prose explanation (filled later)
  metadata jsonb,                 -- detector-specific extras (old_value, new_value, pct_change, etc.)
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_patterns_created_at
  ON patterns(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_patterns_last_seen
  ON patterns(last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_patterns_type
  ON patterns(pattern_type);

CREATE INDEX IF NOT EXISTS idx_patterns_competitors
  ON patterns USING gin(observed_in_competitors);
