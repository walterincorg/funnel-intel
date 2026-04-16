from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel


# --- Competitors ---

class CompetitorCreate(BaseModel):
    name: str
    slug: str
    funnel_url: str
    config: dict | None = None


class CompetitorUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    funnel_url: str | None = None
    config: dict | None = None


class Competitor(BaseModel):
    id: str
    name: str
    slug: str
    funnel_url: str
    config: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- Scan Runs ---

class ScanRunOut(BaseModel):
    id: str
    competitor_id: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_steps: int | None = None
    stop_reason: str | None = None
    summary: dict | None = None
    is_baseline: bool = False
    drift_level: str | None = None
    drift_details: list | None = None
    progress_log: list | None = None
    created_at: datetime | None = None


class ScanTrigger(BaseModel):
    competitor_id: str
    priority: int = 0


# --- Scan Steps ---

class ScanStepOut(BaseModel):
    id: str
    run_id: str
    step_number: int
    step_type: str
    question_text: str | None = None
    answer_options: list | None = None
    action_taken: str | None = None
    url: str | None = None
    screenshot_path: str | None = None
    metadata: dict | None = None
    created_at: datetime | None = None


# --- Pricing ---

class PricingSnapshotOut(BaseModel):
    id: str
    run_id: str
    competitor_id: str
    plans: list | None = None
    discounts: list | None = None
    trial_info: dict | None = None
    captured_at_step: int | None = None
    url: str | None = None
    screenshot_path: str | None = None
    created_at: datetime | None = None


# --- Ads ---

class AdOut(BaseModel):
    id: str
    competitor_id: str
    meta_ad_id: str
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    status: str | None = None
    advertiser_name: str | None = None
    page_id: str | None = None
    media_type: str | None = None
    platforms: list | None = None
    landing_page_url: str | None = None
    created_at: datetime | None = None


class AdSnapshotOut(BaseModel):
    id: str
    ad_id: str
    competitor_id: str
    captured_date: str
    status: str | None = None
    body_text: str | None = None
    headline: str | None = None
    cta: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    start_date: str | None = None
    stop_date: str | None = None
    platforms: list | None = None
    impression_range: dict | None = None
    landing_page_url: str | None = None
    created_at: datetime | None = None


class AdSignalOut(BaseModel):
    id: str
    competitor_id: str
    ad_id: str | None = None
    signal_type: str
    severity: str
    title: str
    detail: str | None = None
    metadata: dict | None = None
    signal_date: str
    created_at: datetime | None = None


class AdScrapeRunOut(BaseModel):
    id: str
    status: str
    competitors_scraped: int = 0
    ads_found: int = 0
    signals_generated: int = 0
    analyses_completed: int = 0
    analyses_failed: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    created_at: datetime | None = None


class AdBriefingOut(BaseModel):
    id: str
    briefing_date: str
    headline: str
    summary: str
    suggested_action: str
    winner_ads: list[dict] = []
    competitor_moves: list[dict] = []
    created_at: datetime | None = None


# --- Domain Intelligence ---

class DomainFingerprintOut(BaseModel):
    id: str
    competitor_id: str
    domain: str
    fingerprint_type: str
    fingerprint_value: str
    detected_at_url: str | None = None
    raw_snippet: str | None = None
    captured_at: datetime | None = None


class OperatorClusterOut(BaseModel):
    id: str
    fingerprint_type: str
    fingerprint_value: str
    detected_at: datetime | None = None
    members: list[dict] = []


class DiscoveredDomainOut(BaseModel):
    id: str
    domain: str
    discovery_source: str
    discovery_reason: str | None = None
    first_seen_at: datetime | None = None
    last_checked_at: datetime | None = None
    status: str = "new"
    alerted_at: datetime | None = None


class DomainIntelRunOut(BaseModel):
    id: str
    status: str
    competitors_scanned: int = 0
    fingerprints_found: int = 0
    clusters_found: int = 0
    domains_discovered: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    created_at: datetime | None = None


# --- Version ---

class VersionOut(BaseModel):
    commit: str
    deployed_at: str
