-- Domain Intelligence — minimal schema.
--
-- Two signals only:
--   1. Brand-prefixed WHOIS monitoring          -> discovered_domains
--   2. Google Analytics + Facebook Pixel match  -> domain_fingerprints + operator_clusters
--
-- Everything else (GTM, hosting, tech stack, reverse lookups, change log) is gone.
-- ⚠️ DESTRUCTIVE on existing installs: drops removed tables and recreates.

DROP TABLE IF EXISTS domain_changes CASCADE;
DROP TABLE IF EXISTS domain_competitor_links CASCADE;
DROP TABLE IF EXISTS cluster_members CASCADE;
DROP TABLE IF EXISTS operator_clusters CASCADE;
DROP TABLE IF EXISTS discovered_domains CASCADE;
DROP TABLE IF EXISTS domain_fingerprints CASCADE;
DROP TABLE IF EXISTS domain_intel_runs CASCADE;

-- Idempotent: competitor brand keyword (exact match for WHOIS prefix search)
ALTER TABLE competitors ADD COLUMN IF NOT EXISTS brand_keyword text;

-- === DOMAIN FINGERPRINTS (GA + Pixel only) ===
CREATE TABLE domain_fingerprints (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  domain text NOT NULL,
  fingerprint_type text NOT NULL CHECK (fingerprint_type IN ('google_analytics', 'facebook_pixel')),
  fingerprint_value text NOT NULL,
  detected_at_url text,
  raw_snippet text,
  captured_at timestamptz DEFAULT now(),
  UNIQUE(competitor_id, fingerprint_type, fingerprint_value)
);
CREATE INDEX idx_fingerprints_value ON domain_fingerprints(fingerprint_value);
CREATE INDEX idx_fingerprints_competitor ON domain_fingerprints(competitor_id);

-- === OPERATOR CLUSTERS (competitors sharing a GA or Pixel code) ===
CREATE TABLE operator_clusters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fingerprint_type text NOT NULL CHECK (fingerprint_type IN ('google_analytics', 'facebook_pixel')),
  fingerprint_value text NOT NULL,
  detected_at timestamptz DEFAULT now(),
  UNIQUE(fingerprint_type, fingerprint_value)
);

CREATE TABLE cluster_members (
  cluster_id uuid REFERENCES operator_clusters(id) ON DELETE CASCADE,
  competitor_id uuid REFERENCES competitors(id) ON DELETE CASCADE,
  added_at timestamptz DEFAULT now(),
  PRIMARY KEY (cluster_id, competitor_id)
);

-- === DISCOVERED DOMAINS (WHOIS brand-prefix matches) ===
CREATE TABLE discovered_domains (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  domain text NOT NULL UNIQUE,
  discovery_source text NOT NULL DEFAULT 'whois_monitor',
  discovery_reason text,
  first_seen_at timestamptz DEFAULT now(),
  last_checked_at timestamptz,
  status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed', 'added_to_tracking', 'dismissed')),
  alerted_at timestamptz
);
CREATE INDEX idx_discovered_domains_alerted ON discovered_domains(alerted_at);

-- === DOMAIN INTEL RUNS (observability) ===
CREATE TABLE domain_intel_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status text NOT NULL DEFAULT 'pending',
  competitors_scanned int DEFAULT 0,
  fingerprints_found int DEFAULT 0,
  clusters_found int DEFAULT 0,
  domains_discovered int DEFAULT 0,
  started_at timestamptz,
  completed_at timestamptz,
  error text,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX idx_domain_intel_runs_status ON domain_intel_runs(status);
