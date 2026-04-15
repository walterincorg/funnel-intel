const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// --- Types ---

export interface Competitor {
  id: string
  name: string
  slug: string
  funnel_url: string
  config: Record<string, unknown> | null
  created_at: string
  updated_at: string | null
}

export interface ProgressLogEntry {
  step: number
  type: string
  message: string
}

export interface ScanRun {
  id: string
  competitor_id: string
  status: string
  started_at: string | null
  completed_at: string | null
  total_steps: number | null
  stop_reason: string | null
  summary: Record<string, unknown> | null
  is_baseline: boolean
  drift_level: string | null
  drift_details: DriftDetail[] | null
  progress_log: ProgressLogEntry[] | null
  created_at: string
}

export interface DriftDetail {
  severity: string
  category: string
  step_number: number | null
  description: string
}

export interface ScanStep {
  id: string
  run_id: string
  step_number: number
  step_type: string
  question_text: string | null
  answer_options: { label: string; value?: string }[] | null
  action_taken: string | null
  url: string | null
  screenshot_path: string | null
  metadata: Record<string, unknown> | null
  created_at: string
}

export interface PricingSnapshot {
  id: string
  run_id: string
  competitor_id: string
  plans: { name: string; price: string; currency: string; period: string; features?: string[] }[] | null
  discounts: { type: string; amount: string; original_price?: string; discounted_price?: string; conditions?: string }[] | null
  trial_info: { has_trial: boolean; trial_days?: number; trial_price?: string } | null
  captured_at_step: number | null
  url: string | null
  screenshot_path: string | null
  created_at: string
}

export interface CompareResult {
  run_a_id: string
  run_b_id: string
  total_steps_a: number
  total_steps_b: number
  step_diffs: StepDiff[]
  pricing_a: PricingSnapshot | null
  pricing_b: PricingSnapshot | null
}

export interface StepDiff {
  step_number: number
  status: 'unchanged' | 'changed' | 'added' | 'removed'
  changes: string[]
  run_a: ScanStep | null
  run_b: ScanStep | null
}

export interface Ad {
  id: string
  competitor_id: string
  meta_ad_id: string
  first_seen_at: string | null
  last_seen_at: string | null
  status: string | null
  advertiser_name: string | null
  page_id: string | null
  media_type: string | null
  platforms: string[] | null
  landing_page_url: string | null
  created_at: string
}

export interface AdSignal {
  id: string
  competitor_id: string
  ad_id: string | null
  signal_type: string
  severity: string
  title: string
  detail: string | null
  metadata: Record<string, unknown> | null
  signal_date: string
  created_at: string
}

export interface AdSignalSummary {
  signal_type: string
  count: number
}

export interface AdSnapshot {
  id: string
  ad_id: string
  competitor_id: string
  captured_date: string
  status: string | null
  body_text: string | null
  headline: string | null
  cta: string | null
  image_url: string | null
  video_url: string | null
  start_date: string | null
  stop_date: string | null
  platforms: string[] | null
  impression_range: unknown | null
  landing_page_url: string | null
  created_at: string
}

export interface AdScrapeRun {
  id: string
  status: string
  competitors_scraped: number
  ads_found: number
  signals_generated: number
  analyses_completed: number
  analyses_failed: number
  started_at: string | null
  completed_at: string | null
  error: string | null
  created_at: string
}

export interface CompetitorAnalysis {
  id: string
  competitor_id: string
  analysis_date: string
  summary: string
  top_ads: { ad_id: string | null; meta_ad_id: string; reason: string }[]
  strategy_tags: string[]
  created_at: string
}

// --- Domain Intelligence ---

export interface DomainFingerprint {
  id: string
  competitor_id: string
  domain: string
  fingerprint_type: string
  fingerprint_value: string
  detected_at_url: string | null
  raw_snippet: string | null
  captured_at: string
}

export interface OperatorCluster {
  id: string
  cluster_name: string | null
  fingerprint_type: string
  fingerprint_value: string
  confidence: string
  detected_at: string
  members: { id: string; name: string; slug: string }[]
}

export interface DiscoveredDomain {
  id: string
  domain: string
  discovery_source: string
  discovery_reason: string | null
  linked_fingerprint_value: string | null
  first_seen_at: string
  last_checked_at: string | null
  status: string
  relevance: string
}

export interface DomainChange {
  id: string
  competitor_id: string
  fingerprint_type: string
  change_type: string
  old_value: string | null
  new_value: string | null
  detected_at: string
}

export interface DomainIntelRun {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  competitors_scanned: number | null
  fingerprints_found: number | null
  clusters_found: number | null
  domains_discovered: number | null
  started_at: string | null
  completed_at: string | null
  error: string | null
  created_at: string
}

export interface DomainStats {
  competitors_tracked: number
  clusters_found: number
  new_domains_7d: number
  shared_codes: number
}

export interface ScanJob {
  id: string
  competitor_id: string
  status: 'pending' | 'picked'
  created_at: string
  picked_at: string | null
}

export interface Version {
  commit: string
  deployed_at: string
}

// --- Ship list / synthesis types ---

export type ShipListOutcomeKind = 'won' | 'lost' | 'inconclusive' | 'not_tested'

export interface ShipListOutcome {
  ship_list_item_id: string
  outcome: ShipListOutcomeKind
  notes: string | null
  recorded_at: string
}

export interface ShipListItem {
  id: string
  week_of: string
  rank: number
  headline: string
  recommendation: string
  test_plan: string
  effort_estimate: 'XS' | 'S' | 'M' | 'L'
  confidence: number
  pattern_ids: string[]
  swipe_file_refs: Array<{ type: string; id: string; label?: string }> | null
  status: 'proposed' | 'shipping' | 'shipped' | 'skipped' | 'expired'
  shipping_at: string | null
  shipped_at: string | null
  outcome_alerted_at: string | null
  generated_by_run_id: string | null
  created_at: string
  latest_outcome: ShipListOutcome | null
}

export interface SynthesisRun {
  id: string
  status: 'pending' | 'running' | 'completed' | 'empty' | 'aborted_stale' | 'failed'
  week_of: string
  trigger: 'scheduled' | 'manual'
  candidate_pattern_count: number | null
  prior_outcome_count: number | null
  stale_sources: Array<Record<string, unknown>> | null
  patterns_found: number | null
  patterns_persisted: number | null
  ship_list_item_count: number | null
  items_rejected_shape: number | null
  items_rejected_citation: number | null
  retries: number | null
  llm_cost_cents: number | null
  input_tokens: number | null
  output_tokens: number | null
  started_at: string | null
  completed_at: string | null
  duration_s: number | null
  error: string | null
  created_at: string
}

export interface ShipListResponse {
  week_of: string | null
  items: ShipListItem[]
  run: SynthesisRun | null
  available_weeks: Array<{ week_of: string; item_count: number }>
  is_stale: boolean
  stale_source_count: number
}

export interface FreshnessRow {
  source: string
  competitor_id: string
  last_success_at: string | null
  last_failure_at: string | null
  last_error: string | null
  updated_at: string
  is_stale: boolean
}

export interface FreshnessResponse {
  rows: FreshnessRow[]
  stale_count: number
}

// --- API Functions ---

export const api = {
  // Competitors
  listCompetitors: () => request<Competitor[]>('/competitors'),
  getCompetitor: (id: string) => request<Competitor>(`/competitors/${id}`),
  createCompetitor: (data: { name: string; slug: string; funnel_url: string; config?: Record<string, unknown> }) =>
    request<Competitor>('/competitors', { method: 'POST', body: JSON.stringify(data) }),
  updateCompetitor: (id: string, data: Partial<Competitor>) =>
    request<Competitor>(`/competitors/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteCompetitor: (id: string) =>
    request<void>(`/competitors/${id}`, { method: 'DELETE' }),

  // Scans
  listScans: (competitorId?: string) =>
    request<ScanRun[]>(competitorId ? `/scans?competitor_id=${competitorId}` : '/scans'),
  getScan: (id: string) => request<ScanRun>(`/scans/${id}`),
  getScanSteps: (runId: string) => request<ScanStep[]>(`/scans/${runId}/steps`),
  triggerScan: (competitorId: string) =>
    request<{ job_id: string; status: string }>('/scans/trigger', {
      method: 'POST',
      body: JSON.stringify({ competitor_id: competitorId }),
    }),
  listActiveJobs: () => request<ScanJob[]>('/scans/jobs/active'),

  // Pricing
  listPricing: (competitorId?: string) =>
    request<PricingSnapshot[]>(competitorId ? `/pricing?competitor_id=${competitorId}` : '/pricing'),
  latestPricing: () => request<PricingSnapshot[]>('/pricing/latest'),

  // Compare
  compareRuns: (runAId: string, runBId: string) =>
    request<CompareResult>(`/compare/${runAId}/${runBId}`),

  // Ads
  listAds: (competitorId?: string) =>
    request<Ad[]>(competitorId ? `/ads?competitor_id=${competitorId}` : '/ads'),
  getAd: (id: string) => request<Ad>(`/ads/${id}`),
  getAdSnapshots: (adId: string) => request<AdSnapshot[]>(`/ads/${adId}/snapshots`),
  listAdSignals: (params?: { competitor_id?: string; signal_type?: string; days?: number }) => {
    const qs = new URLSearchParams()
    if (params?.competitor_id) qs.set('competitor_id', params.competitor_id)
    if (params?.signal_type) qs.set('signal_type', params.signal_type)
    if (params?.days) qs.set('days', String(params.days))
    return request<AdSignal[]>(`/ads/signals?${qs}`)
  },
  adSignalsSummary: (days?: number) =>
    request<AdSignalSummary[]>(days ? `/ads/signals/summary?days=${days}` : '/ads/signals/summary'),
  listAdScrapeRuns: () => request<AdScrapeRun[]>('/ads/scrape-runs'),
  listAnalyses: (competitorId?: string) =>
    request<CompetitorAnalysis[]>(competitorId ? `/ads/analysis?competitor_id=${competitorId}` : '/ads/analysis'),
  triggerAdScrape: () =>
    request<{ run_id: string; status: string }>('/ads/scrape/trigger', { method: 'POST' }),

  // Domain Intelligence
  domainStats: () => request<DomainStats>('/domains/stats'),
  listFingerprints: (params?: { competitor_id?: string; shared_only?: boolean }) => {
    const qs = new URLSearchParams()
    if (params?.competitor_id) qs.set('competitor_id', params.competitor_id)
    if (params?.shared_only) qs.set('shared_only', 'true')
    return request<DomainFingerprint[]>(`/domains/fingerprints?${qs}`)
  },
  listClusters: (minConfidence?: string) =>
    request<OperatorCluster[]>(minConfidence ? `/domains/clusters?min_confidence=${minConfidence}` : '/domains/clusters'),
  listDiscoveredDomains: (params?: { days?: number; min_relevance?: string }) => {
    const qs = new URLSearchParams()
    if (params?.days) qs.set('days', String(params.days))
    if (params?.min_relevance) qs.set('min_relevance', params.min_relevance)
    return request<DiscoveredDomain[]>(`/domains/discovered?${qs}`)
  },
  listDomainChanges: (params?: { competitor_id?: string; days?: number }) => {
    const qs = new URLSearchParams()
    if (params?.competitor_id) qs.set('competitor_id', params.competitor_id)
    if (params?.days) qs.set('days', String(params.days))
    return request<DomainChange[]>(`/domains/changes?${qs}`)
  },
  domainRuns: () => request<DomainIntelRun[]>('/domains/runs'),
  triggerDomainScan: () =>
    request<{ run_id: string; status: string }>('/domains/scan', { method: 'POST' }),

  // Ship list / synthesis
  getShipList: (week?: string) =>
    request<ShipListResponse>(week ? `/ship-list?week=${week}` : '/ship-list'),
  listShipListWeeks: () =>
    request<Array<{ week_of: string; item_count: number }>>('/ship-list/weeks'),
  updateShipItemStatus: (id: string, status: ShipListItem['status']) =>
    request<ShipListItem>(`/ship-list/${id}/status`, {
      method: 'POST',
      body: JSON.stringify({ status }),
    }),
  recordShipItemOutcome: (
    id: string,
    outcome: 'won' | 'lost' | 'inconclusive' | 'not_tested',
    notes?: string,
  ) =>
    request<{ id: string }>(`/ship-list/${id}/outcome`, {
      method: 'POST',
      body: JSON.stringify({ outcome, notes }),
    }),
  listSynthesisRuns: () => request<SynthesisRun[]>('/ship-list/synthesis-runs'),
  triggerSynthesis: () =>
    request<{ run_id: string; status: string }>('/ship-list/synthesis/trigger', {
      method: 'POST',
    }),
  getFreshness: () => request<FreshnessResponse>('/ship-list/freshness'),

  // System
  version: () => request<Version>('/version'),
}
