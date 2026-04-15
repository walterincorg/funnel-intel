-- Creative similarity clusters — per-competitor SimHash groups of near-duplicate ads.
--
-- Why: DTC operators routinely re-upload winning ads with a word or two changed.
-- Treating each upload as "new" inflates new-ad counts and drowns signal in noise.
-- The synthesis layer needs to see "BetterMe launched 3 angles (7 variants each)"
-- not "BetterMe launched 21 new ads."
--
-- How: SimHash is a locality-sensitive hash of the ad's text features (headline +
-- body + cta). Two ads differing by a word have hamming distance ≤ 3-5 out of 64
-- bits (>92% similarity). On ingest we compute each ad's simhash, then scan the
-- competitor's existing clusters and join the closest one within threshold, or
-- create a new cluster.
--
-- Clusters are scoped per-competitor. Two different brands converging on similar
-- copy is a pattern for the synthesis layer to surface, not noise to dedup away.
--
-- Storage: simhash stored as bigint (signed int64). Python computes unsigned
-- 64-bit; convert at the boundary via helpers in creative_cluster.py.

CREATE TABLE IF NOT EXISTS creative_clusters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
  centroid_simhash bigint NOT NULL,
  representative_ad_id uuid REFERENCES ads(id) ON DELETE SET NULL,
  member_count int NOT NULL DEFAULT 1,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_creative_clusters_competitor
  ON creative_clusters(competitor_id);

-- Each ad belongs to exactly one cluster (primary key on ad_id enforces this).
CREATE TABLE IF NOT EXISTS ad_cluster_members (
  ad_id uuid PRIMARY KEY REFERENCES ads(id) ON DELETE CASCADE,
  cluster_id uuid NOT NULL REFERENCES creative_clusters(id) ON DELETE CASCADE,
  simhash bigint NOT NULL,
  similarity real NOT NULL,
  assigned_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ad_cluster_members_cluster
  ON ad_cluster_members(cluster_id);
