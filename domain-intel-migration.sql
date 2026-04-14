-- Domain Intelligence — new tables for infrastructure fingerprinting + domain discovery

-- === DOMAIN FINGERPRINTS (tracking codes + tech stack per competitor) ===
CREATE TABLE domain_fingerprints (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  domain text NOT NULL,
  fingerprint_type text NOT NULL,  -- 'google_analytics', 'facebook_pixel', 'gtm', 'hosting', 'tech_stack'
  fingerprint_value text NOT NULL,
  detected_at_url text,
  raw_snippet text,
  metadata jsonb,
  captured_at timestamptz DEFAULT now(),
  UNIQUE(competitor_id, fingerprint_type, fingerprint_value)
);
CREATE INDEX idx_fingerprints_value ON domain_fingerprints(fingerprint_value);
CREATE INDEX idx_fingerprints_competitor ON domain_fingerprints(competitor_id);

-- === OPERATOR CLUSTERS (groups of competitors sharing tracking codes) ===
CREATE TABLE operator_clusters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cluster_name text,
  fingerprint_type text NOT NULL,
  fingerprint_value text NOT NULL,
  confidence text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
  detected_at timestamptz DEFAULT now(),
  UNIQUE(fingerprint_type, fingerprint_value)
);

CREATE TABLE cluster_members (
  cluster_id uuid REFERENCES operator_clusters(id) ON DELETE CASCADE,
  competitor_id uuid REFERENCES competitors(id) ON DELETE CASCADE,
  added_at timestamptz DEFAULT now(),
  PRIMARY KEY (cluster_id, competitor_id)
);

-- === DISCOVERED DOMAINS (from reverse lookups + WHOIS monitoring) ===
CREATE TABLE discovered_domains (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  domain text NOT NULL UNIQUE,
  discovery_source text NOT NULL,       -- 'reverse_lookup', 'whois_monitor', 'keyword_match'
  discovery_reason text,                -- e.g., 'shares GA ID G-BM7X92K1 with BetterMe'
  linked_fingerprint_value text,
  whois_data jsonb,
  first_seen_at timestamptz DEFAULT now(),
  last_checked_at timestamptz,
  status text DEFAULT 'new',            -- 'new', 'reviewed', 'added_to_tracking', 'dismissed'
  relevance text DEFAULT 'medium',      -- 'high', 'medium', 'low'
  CONSTRAINT valid_status CHECK (status IN ('new', 'reviewed', 'added_to_tracking', 'dismissed')),
  CONSTRAINT valid_relevance CHECK (relevance IN ('high', 'medium', 'low'))
);
CREATE INDEX idx_discovered_linked_fingerprint ON discovered_domains(linked_fingerprint_value);

CREATE TABLE domain_competitor_links (
  domain_id uuid REFERENCES discovered_domains(id) ON DELETE CASCADE,
  competitor_id uuid REFERENCES competitors(id) ON DELETE CASCADE,
  link_reason text,  -- 'shared_ga', 'shared_pixel', 'whois_match'
  PRIMARY KEY (domain_id, competitor_id)
);

-- === DOMAIN CHANGES (fingerprint change log) ===
CREATE TABLE domain_changes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  fingerprint_type text NOT NULL,
  change_type text NOT NULL,  -- 'code_added', 'code_removed', 'hosting_changed', 'tech_changed'
  old_value text,
  new_value text,
  detected_at timestamptz DEFAULT now()
);
CREATE INDEX idx_domain_changes_competitor ON domain_changes(competitor_id);
CREATE INDEX idx_domain_changes_date ON domain_changes(detected_at DESC);

-- === DOMAIN INTEL RUNS (observability per extraction cycle) ===
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
