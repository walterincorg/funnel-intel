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

export interface Version {
  commit: string
  deployed_at: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatResponse {
  reply: string
}

export type ChatModelPreset = 'basic' | 'advanced' | 'expert' | 'genius'

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

  // Pricing
  listPricing: (competitorId?: string) =>
    request<PricingSnapshot[]>(competitorId ? `/pricing?competitor_id=${competitorId}` : '/pricing'),
  latestPricing: () => request<PricingSnapshot[]>('/pricing/latest'),

  // Compare
  compareRuns: (runAId: string, runBId: string) =>
    request<CompareResult>(`/compare/${runAId}/${runBId}`),

  // System
  version: () => request<Version>('/version'),

  // Chat
  chat: (
    message: string,
    history: ChatMessage[] = [],
    modelPreset: ChatModelPreset = 'advanced',
    signal?: AbortSignal,
  ) =>
    request<ChatResponse>('/chat', {
      method: 'POST',
      signal,
      body: JSON.stringify({ message, history, model_preset: modelPreset }),
    }),
}
