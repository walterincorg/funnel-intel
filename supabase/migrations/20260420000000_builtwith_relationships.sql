CREATE TABLE builtwith_relationships (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  competitor_id uuid REFERENCES competitors(id) ON DELETE CASCADE,
  source_domain text NOT NULL,
  related_domain text NOT NULL,
  attribute_value text,
  first_detected text,
  last_detected text,
  overlap_duration text,
  scraped_at timestamptz DEFAULT now(),
  UNIQUE (competitor_id, related_domain, attribute_value)
);
CREATE INDEX idx_bw_rels_competitor ON builtwith_relationships (competitor_id);
CREATE INDEX idx_bw_rels_related ON builtwith_relationships (related_domain);
