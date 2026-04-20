CREATE TABLE IF NOT EXISTS app_settings (
  id int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  funnel_scan_interval_minutes int NOT NULL DEFAULT 90,
  funnel_scan_enabled boolean NOT NULL DEFAULT true,
  ad_scrape_enabled boolean NOT NULL DEFAULT false,
  ad_scrape_hour_utc int NOT NULL DEFAULT 6 CHECK (ad_scrape_hour_utc BETWEEN 0 AND 23),
  ad_scrape_days_of_week int[] NOT NULL DEFAULT '{0,3}',
  domain_intel_enabled boolean NOT NULL DEFAULT true,
  domain_intel_day_of_week int NOT NULL DEFAULT 1 CHECK (domain_intel_day_of_week BETWEEN 0 AND 6),
  domain_intel_hour_utc int NOT NULL DEFAULT 7 CHECK (domain_intel_hour_utc BETWEEN 0 AND 23),
  updated_at timestamptz DEFAULT now()
);

INSERT INTO app_settings (id) VALUES (1) ON CONFLICT DO NOTHING;
