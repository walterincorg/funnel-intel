-- Meta Ad Library Tracking — new tables for ad intelligence pipeline

-- Add ads_library_url to competitors
ALTER TABLE competitors ADD COLUMN IF NOT EXISTS ads_library_url text;

-- === ADS (canonical ad entity, one row per meta_ad_id per competitor) ===
CREATE TABLE ads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  meta_ad_id text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  status text,
  advertiser_name text,
  page_id text,
  media_type text,
  platforms jsonb,
  landing_page_url text,
  created_at timestamptz DEFAULT now(),
  UNIQUE(competitor_id, meta_ad_id)
);

-- === AD SNAPSHOTS (daily state capture for diffing) ===
CREATE TABLE ad_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ad_id uuid NOT NULL REFERENCES ads(id) ON DELETE CASCADE,
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  captured_date date NOT NULL DEFAULT CURRENT_DATE,
  status text,
  body_text text,
  headline text,
  cta text,
  image_url text,
  video_url text,
  start_date date,
  stop_date date,
  platforms jsonb,
  impression_range jsonb,
  landing_page_url text,
  raw_data jsonb,
  created_at timestamptz DEFAULT now(),
  UNIQUE(ad_id, captured_date)
);

-- === AD SIGNALS (derived events) ===
CREATE TABLE ad_signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  ad_id uuid REFERENCES ads(id) ON DELETE SET NULL,
  signal_type text NOT NULL,
  severity text NOT NULL DEFAULT 'medium',
  title text NOT NULL,
  detail text,
  metadata jsonb,
  signal_date date NOT NULL DEFAULT CURRENT_DATE,
  created_at timestamptz DEFAULT now()
);

-- === AD SCRAPE RUNS (observability per Apify invocation) ===
CREATE TABLE ad_scrape_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status text NOT NULL DEFAULT 'pending',
  competitors_scraped int DEFAULT 0,
  ads_found int DEFAULT 0,
  signals_generated int DEFAULT 0,
  started_at timestamptz,
  completed_at timestamptz,
  error text,
  created_at timestamptz DEFAULT now()
);

-- === INDEXES ===
CREATE INDEX idx_ads_competitor ON ads(competitor_id);
CREATE INDEX idx_ads_meta_id ON ads(competitor_id, meta_ad_id);
CREATE INDEX idx_ads_last_seen ON ads(last_seen_at);
CREATE INDEX idx_ad_snapshots_ad ON ad_snapshots(ad_id);
CREATE INDEX idx_ad_snapshots_date ON ad_snapshots(captured_date);
CREATE INDEX idx_ad_snapshots_competitor_date ON ad_snapshots(competitor_id, captured_date);
CREATE INDEX idx_ad_signals_competitor ON ad_signals(competitor_id);
CREATE INDEX idx_ad_signals_date ON ad_signals(signal_date DESC);
CREATE INDEX idx_ad_signals_type ON ad_signals(signal_type);
CREATE INDEX idx_ad_scrape_runs_status ON ad_scrape_runs(status);
