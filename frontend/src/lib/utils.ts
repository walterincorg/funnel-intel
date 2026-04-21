import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import type { DomainIntelRun } from '@/api/client'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDate(date: string | null | undefined): string {
  if (!date) return '—'
  return new Date(date).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function checkActive(lastDetected: string | null): boolean {
  if (!lastDetected) return false
  if (lastDetected.toLowerCase().includes('current')) return true
  const d = new Date(lastDetected)
  if (isNaN(d.getTime())) return false
  // BuiltWith reports month-granular dates ("Mar 2026") which parse to the 1st
  // of that month, AND they don't re-crawl every site each week. Using a 3-month
  // window matches their data cadence without losing genuinely dead relationships.
  const cutoff = new Date()
  cutoff.setMonth(cutoff.getMonth() - 3)
  return d >= cutoff
}

export function getPrevRunCutoff(runs: DomainIntelRun[]): string | null {
  const completed = runs.filter(r => r.status === 'completed')
  return completed[1]?.completed_at ?? null
}

export function severityColor(severity: string): string {
  switch (severity) {
    case 'critical': return 'text-danger'
    case 'high': return 'text-warning'
    case 'medium': return 'text-info'
    case 'low': return 'text-text'
    default: return 'text-text'
  }
}
