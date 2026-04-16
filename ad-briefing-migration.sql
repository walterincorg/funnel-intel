-- Ad Briefings: cross-competitor CEO summaries (replaces per-competitor analyses)
-- Run after ad-tracking-migration.sql

CREATE TABLE IF NOT EXISTS ad_briefings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    briefing_date date NOT NULL UNIQUE,
    headline text NOT NULL,
    summary text NOT NULL,
    suggested_action text NOT NULL,
    winner_ads jsonb NOT NULL DEFAULT '[]',
    competitor_moves jsonb NOT NULL DEFAULT '[]',
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ad_briefings_date ON ad_briefings(briefing_date DESC);
