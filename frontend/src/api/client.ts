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
  brand_keyword: string | null
  ads_library_url: string | null
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

export interface AdBriefing {
  id: string
  briefing_date: string
  headline: string
  summary: string
  suggested_action: string
  winner_ads: { ad_id: string; meta_ad_id: string; competitor_name: string }[]
  competitor_moves: { competitor_name: string; move_summary: string }[]
  created_at: string
}

export interface WinnerAd {
  ad_id: string
  meta_ad_id: string
  competitor_id: string
  competitor_name: string
  media_type: string | null
  headline: string | null
  body_text: string | null
  image_url: string | null
  video_url: string | null
  cta: string | null
  days_active: number
  landing_page_url: string | null
}

// --- Domain Intelligence ---

export interface DomainFingerprint {
  id: string
  competitor_id: string
  domain: string
  fingerprint_type: 'google_analytics' | 'facebook_pixel'
  fingerprint_value: string
  detected_at_url: string | null
  raw_snippet: string | null
  captured_at: string
}

export interface OperatorCluster {
  id: string
  fingerprint_type: 'google_analytics' | 'facebook_pixel'
  fingerprint_value: string
  detected_at: string
  members: { id: string; name: string; slug: string }[]
}

export interface DiscoveredDomain {
  id: string
  domain: string
  discovery_source: string
  discovery_reason: string | null
  first_seen_at: string
  last_checked_at: string | null
  status: string
  alerted_at: string | null
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

export interface AppSettings {
  funnel_scan_interval_minutes: number
  funnel_scan_enabled: boolean
  ad_scrape_enabled: boolean
  ad_scrape_hour_utc: number
  ad_scrape_days_of_week: number[]
  domain_intel_enabled: boolean
  domain_intel_day_of_week: number
  domain_intel_hour_utc: number
  updated_at: string | null
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

// --- API Functions ---

export const api = {
  // Competitors
  listCompetitors: () => request<Competitor[]>('/competitors'),
  getCompetitor: (id: string) => request<Competitor>(`/competitors/${id}`),
  createCompetitor: (data: { name: string; slug: string; funnel_url: string; brand_keyword?: string; ads_library_url?: string; config?: Record<string, unknown> }) =>
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
  getBriefing: () => request<AdBriefing | null>('/ads/briefing'),
  listWinners: (limit?: number, period?: 'all-time' | 'recent') => {
    const qs = new URLSearchParams()
    if (limit) qs.set('limit', String(limit))
    if (period) qs.set('period', period)
    return request<WinnerAd[]>(`/ads/winners?${qs}`)
  },
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
  listClusters: () => request<OperatorCluster[]>('/domains/clusters'),
  listDiscoveredDomains: (params?: { days?: number }) => {
    const qs = new URLSearchParams()
    if (params?.days) qs.set('days', String(params.days))
    return request<DiscoveredDomain[]>(`/domains/discovered?${qs}`)
  },
  domainRuns: () => request<DomainIntelRun[]>('/domains/runs'),
  triggerDomainScan: () =>
    request<{ run_id: string; status: string }>('/domains/scan', { method: 'POST' }),

  // Settings
  getSettings: () => request<AppSettings>('/settings'),
  updateSettings: (data: Partial<AppSettings>) =>
    request<AppSettings>('/settings', { method: 'PATCH', body: JSON.stringify(data) }),

  // System
  version: () => request<Version>('/version'),
}
