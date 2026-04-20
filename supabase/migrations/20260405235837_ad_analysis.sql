-- Ad Analysis: LLM-generated competitor strategy summaries
-- Run after ad-tracking-migration.sql

CREATE TABLE IF NOT EXISTS competitor_analyses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor_id uuid NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    analysis_date date NOT NULL,
    summary text NOT NULL,
    top_ads jsonb NOT NULL DEFAULT '[]',
    strategy_tags text[] NOT NULL DEFAULT '{}',
    created_at timestamptz DEFAULT now(),
    UNIQUE (competitor_id, analysis_date)
);

CREATE INDEX IF NOT EXISTS idx_competitor_analyses_competitor ON competitor_analyses(competitor_id);
CREATE INDEX IF NOT EXISTS idx_competitor_analyses_date ON competitor_analyses(analysis_date DESC);

-- Add analyses tracking to scrape runs
ALTER TABLE ad_scrape_runs ADD COLUMN IF NOT EXISTS analyses_completed int DEFAULT 0;
ALTER TABLE ad_scrape_runs ADD COLUMN IF NOT EXISTS analyses_failed int DEFAULT 0;
