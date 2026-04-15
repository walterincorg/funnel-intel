-- Per-source freshness tracking for the synthesis layer.
--
-- Every worker loop (ad scrape, domain intel, funnel scan) writes a row here
-- on success and on failure. The weekly synthesis run reads this table as its
-- first gate: if any competitor/source is stale beyond FRESHNESS_STALE_HOURS,
-- abort with aborted_stale and surface the list to the user.
--
-- Design:
--   source           text   'ad_scrape' | 'domain_intel' | 'funnel_scan'
--   competitor_id    uuid   per-competitor row (NULL for global sources if ever added)
--   last_success_at  timestamptz  most recent successful run finish time
--   last_failure_at  timestamptz  most recent failure time
--   last_error       text   short error string from the failing run
--
-- Staleness rule: a source is fresh iff
--   last_success_at IS NOT NULL
--   AND last_success_at > now() - interval '{FRESHNESS_STALE_HOURS} hours'
--   AND (last_failure_at IS NULL OR last_failure_at <= last_success_at)

CREATE TABLE IF NOT EXISTS data_freshness (
  source TEXT NOT NULL,
  competitor_id UUID REFERENCES competitors(id) ON DELETE CASCADE,
  last_success_at TIMESTAMPTZ,
  last_failure_at TIMESTAMPTZ,
  last_error TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source, competitor_id)
);

CREATE INDEX IF NOT EXISTS idx_data_freshness_last_success
  ON data_freshness(source, last_success_at DESC);
