-- ============================================================
-- Funnel Intel — Consolidated Schema
-- Generated: 2026-04-27
-- Replaces all individual migrations in supabase/migrations/
--
-- Use this on a fresh Supabase project (SQL Editor → Run).
-- All statements are idempotent (IF NOT EXISTS / ON CONFLICT).
-- ============================================================


-- ── COMPETITORS ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS competitors (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  name            text        NOT NULL,
  slug            text        UNIQUE NOT NULL,
  funnel_url      text        NOT NULL,
  brand_keyword   text,
  ads_library_url text,
  config          jsonb,
  created_at      timestamptz DEFAULT now(),
  updated_at      timestamptz DEFAULT now()
);


-- ── SCAN RUNS ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS scan_runs (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id   uuid        NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  status          text        NOT NULL DEFAULT 'pending',
  started_at      timestamptz,
  completed_at    timestamptz,
  total_steps     int,
  stop_reason     text,
  summary         jsonb,
  is_baseline     boolean     DEFAULT false,
  baseline_run_id uuid        REFERENCES scan_runs(id),
  drift_level     text,
  drift_details   jsonb,
  progress_log    jsonb       DEFAULT '[]'::jsonb,
  created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scan_runs_competitor ON scan_runs(competitor_id);
CREATE INDEX IF NOT EXISTS idx_scan_runs_status     ON scan_runs(status);
CREATE INDEX IF NOT EXISTS idx_scan_runs_baseline   ON scan_runs(competitor_id) WHERE is_baseline = true;


-- ── SCAN STEPS ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS scan_steps (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id          uuid        NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
  step_number     int         NOT NULL,
  step_type       text        NOT NULL,
  question_text   text,
  answer_options  jsonb,
  action_taken    text,
  url             text,
  screenshot_path text,
  metadata        jsonb,
  created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scan_steps_run        ON scan_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_scan_steps_run_number ON scan_steps(run_id, step_number);


-- ── PRICING SNAPSHOTS ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pricing_snapshots (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id          uuid        NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
  competitor_id   uuid        NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  plans           jsonb,
  discounts       jsonb,
  trial_info      jsonb,
  metadata        jsonb,
  captured_at_step int,
  url             text,
  screenshot_path text,
  created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pricing_competitor ON pricing_snapshots(competitor_id);

ALTER TABLE pricing_snapshots
  ADD COLUMN IF NOT EXISTS metadata jsonb;


-- ── SCAN JOBS (queue) ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS scan_jobs (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid        NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  status        text        DEFAULT 'pending',
  priority      int         DEFAULT 0,
  traversal_model text      NOT NULL DEFAULT 'gpt-5.4-mini',
  picked_at     timestamptz,
  created_at    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scan_jobs_pending ON scan_jobs(status, priority DESC, created_at);

ALTER TABLE scan_jobs
  ADD COLUMN IF NOT EXISTS traversal_model text NOT NULL DEFAULT 'gpt-5.4-mini';

ALTER TABLE scan_jobs
  DROP CONSTRAINT IF EXISTS scan_jobs_traversal_model_check;


-- ── ADS (canonical ad entity) ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ads (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id   uuid        NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  meta_ad_id      text        NOT NULL,
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at    timestamptz NOT NULL DEFAULT now(),
  status          text,
  advertiser_name text,
  page_id         text,
  media_type      text,
  platforms       jsonb,
  landing_page_url text,
  created_at      timestamptz DEFAULT now(),
  UNIQUE(competitor_id, meta_ad_id)
);

CREATE INDEX IF NOT EXISTS idx_ads_competitor ON ads(competitor_id);
CREATE INDEX IF NOT EXISTS idx_ads_meta_id    ON ads(competitor_id, meta_ad_id);
CREATE INDEX IF NOT EXISTS idx_ads_last_seen  ON ads(last_seen_at);


-- ── AD SNAPSHOTS (daily state for diffing) ────────────────────────────────────

CREATE TABLE IF NOT EXISTS ad_snapshots (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  ad_id            uuid        NOT NULL REFERENCES ads(id) ON DELETE CASCADE,
  competitor_id    uuid        NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  captured_date    date        NOT NULL DEFAULT CURRENT_DATE,
  status           text,
  body_text        text,
  headline         text,
  cta              text,
  image_url        text,
  video_url        text,
  start_date       date,
  stop_date        date,
  platforms        jsonb,
  impression_range jsonb,
  landing_page_url text,
  raw_data         jsonb,
  created_at       timestamptz DEFAULT now(),
  UNIQUE(ad_id, captured_date)
);

CREATE INDEX IF NOT EXISTS idx_ad_snapshots_ad              ON ad_snapshots(ad_id);
CREATE INDEX IF NOT EXISTS idx_ad_snapshots_date            ON ad_snapshots(captured_date);
CREATE INDEX IF NOT EXISTS idx_ad_snapshots_competitor_date ON ad_snapshots(competitor_id, captured_date);


-- ── AD SIGNALS (derived events) ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ad_signals (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid        NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  ad_id         uuid        REFERENCES ads(id) ON DELETE SET NULL,
  signal_type   text        NOT NULL,
  severity      text        NOT NULL DEFAULT 'medium',
  title         text        NOT NULL,
  detail        text,
  metadata      jsonb,
  signal_date   date        NOT NULL DEFAULT CURRENT_DATE,
  created_at    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ad_signals_competitor ON ad_signals(competitor_id);
CREATE INDEX IF NOT EXISTS idx_ad_signals_date       ON ad_signals(signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_ad_signals_type       ON ad_signals(signal_type);


-- ── AD SCRAPE RUNS (Apify invocation observability) ───────────────────────────

CREATE TABLE IF NOT EXISTS ad_scrape_runs (
  id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  status               text        NOT NULL DEFAULT 'pending',
  competitors_scraped  int         DEFAULT 0,
  ads_found            int         DEFAULT 0,
  signals_generated    int         DEFAULT 0,
  analyses_completed   int         DEFAULT 0,
  analyses_failed      int         DEFAULT 0,
  started_at           timestamptz,
  completed_at         timestamptz,
  error                text,
  created_at           timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ad_scrape_runs_status ON ad_scrape_runs(status);


-- ── COMPETITOR ANALYSES (LLM strategy summaries) ──────────────────────────────

CREATE TABLE IF NOT EXISTS competitor_analyses (
  id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id  uuid        NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  analysis_date  date        NOT NULL,
  summary        text        NOT NULL,
  top_ads        jsonb       NOT NULL DEFAULT '[]',
  strategy_tags  text[]      NOT NULL DEFAULT '{}',
  created_at     timestamptz DEFAULT now(),
  UNIQUE(competitor_id, analysis_date)
);

CREATE INDEX IF NOT EXISTS idx_competitor_analyses_competitor ON competitor_analyses(competitor_id);
CREATE INDEX IF NOT EXISTS idx_competitor_analyses_date       ON competitor_analyses(analysis_date DESC);


-- ── DOMAIN FINGERPRINTS (GA + Pixel matches) ──────────────────────────────────

CREATE TABLE IF NOT EXISTS domain_fingerprints (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id    uuid        NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  domain           text        NOT NULL,
  fingerprint_type text        NOT NULL CHECK (fingerprint_type IN ('google_analytics', 'facebook_pixel', 'gtm')),
  fingerprint_value text       NOT NULL,
  detected_at_url  text,
  raw_snippet      text,
  captured_at      timestamptz DEFAULT now(),
  UNIQUE(competitor_id, fingerprint_type, fingerprint_value)
);

CREATE INDEX IF NOT EXISTS idx_fingerprints_value      ON domain_fingerprints(fingerprint_value);
CREATE INDEX IF NOT EXISTS idx_fingerprints_competitor ON domain_fingerprints(competitor_id);


-- ── OPERATOR CLUSTERS (competitors sharing a GA / Pixel code) ────────────────

CREATE TABLE IF NOT EXISTS operator_clusters (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  fingerprint_type  text        NOT NULL CHECK (fingerprint_type IN ('google_analytics', 'facebook_pixel', 'gtm')),
  fingerprint_value text        NOT NULL,
  detected_at       timestamptz DEFAULT now(),
  UNIQUE(fingerprint_type, fingerprint_value)
);

CREATE TABLE IF NOT EXISTS cluster_members (
  cluster_id    uuid REFERENCES operator_clusters(id) ON DELETE CASCADE,
  competitor_id uuid REFERENCES competitors(id) ON DELETE CASCADE,
  added_at      timestamptz DEFAULT now(),
  PRIMARY KEY (cluster_id, competitor_id)
);


-- ── DISCOVERED DOMAINS (WHOIS brand-prefix monitoring) ───────────────────────

CREATE TABLE IF NOT EXISTS discovered_domains (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  domain           text        NOT NULL UNIQUE,
  discovery_source text        NOT NULL DEFAULT 'whois_monitor',
  discovery_reason text,
  first_seen_at    timestamptz DEFAULT now(),
  last_checked_at  timestamptz,
  status           text        NOT NULL DEFAULT 'new'
    CHECK (status IN ('new', 'reviewed', 'added_to_tracking', 'dismissed')),
  alerted_at       timestamptz
);

CREATE INDEX IF NOT EXISTS idx_discovered_domains_alerted ON discovered_domains(alerted_at);


-- ── DOMAIN INTEL RUNS (observability) ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS domain_intel_runs (
  id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  status               text        NOT NULL DEFAULT 'pending',
  competitors_scanned  int         DEFAULT 0,
  fingerprints_found   int         DEFAULT 0,
  clusters_found       int         DEFAULT 0,
  domains_discovered   int         DEFAULT 0,
  started_at           timestamptz,
  completed_at         timestamptz,
  error                text,
  created_at           timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_domain_intel_runs_status ON domain_intel_runs(status);


-- ── AD BRIEFINGS (cross-competitor CEO summaries) ────────────────────────────

CREATE TABLE IF NOT EXISTS ad_briefings (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  briefing_date    date        NOT NULL UNIQUE,
  headline         text        NOT NULL,
  summary          text        NOT NULL,
  suggested_action text        NOT NULL,
  winner_ads       jsonb       NOT NULL DEFAULT '[]',
  competitor_moves jsonb       NOT NULL DEFAULT '[]',
  created_at       timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ad_briefings_date ON ad_briefings(briefing_date DESC);


-- ── APP SETTINGS (singleton row) ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS app_settings (
  id                            int     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  funnel_scan_interval_minutes  int     NOT NULL DEFAULT 90,
  funnel_scan_enabled           boolean NOT NULL DEFAULT true,
  ad_scrape_enabled             boolean NOT NULL DEFAULT false,
  ad_scrape_hour_utc            int     NOT NULL DEFAULT 6 CHECK (ad_scrape_hour_utc BETWEEN 0 AND 23),
  ad_scrape_days_of_week        int[]   NOT NULL DEFAULT '{0,3}',
  domain_intel_enabled          boolean NOT NULL DEFAULT true,
  domain_intel_day_of_week      int     NOT NULL DEFAULT 1 CHECK (domain_intel_day_of_week BETWEEN 0 AND 6),
  domain_intel_hour_utc         int     NOT NULL DEFAULT 7 CHECK (domain_intel_hour_utc BETWEEN 0 AND 23),
  updated_at                    timestamptz DEFAULT now()
);

INSERT INTO app_settings (id) VALUES (1) ON CONFLICT DO NOTHING;


-- ── BUILTWITH RELATIONSHIPS ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS builtwith_relationships (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id    uuid        REFERENCES competitors(id) ON DELETE CASCADE,
  source_domain    text        NOT NULL,
  related_domain   text        NOT NULL,
  attribute_value  text,
  first_detected   text,
  last_detected    text,
  overlap_duration text,
  first_seen_at    timestamptz DEFAULT now(),
  scraped_at       timestamptz DEFAULT now(),
  UNIQUE(competitor_id, related_domain, attribute_value)
);

CREATE INDEX IF NOT EXISTS idx_bw_rels_competitor ON builtwith_relationships(competitor_id);
CREATE INDEX IF NOT EXISTS idx_bw_rels_related    ON builtwith_relationships(related_domain);


-- ── STORAGE BUCKETS ───────────────────────────────────────────────────────────
-- Private bucket used for scan screenshots and other captured artifacts.

INSERT INTO storage.buckets (id, name, public)
VALUES ('funnel-screenshots', 'funnel-screenshots', false)
ON CONFLICT (id) DO NOTHING;
