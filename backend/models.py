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


# --- Version ---

class VersionOut(BaseModel):
    commit: str
    deployed_at: str
