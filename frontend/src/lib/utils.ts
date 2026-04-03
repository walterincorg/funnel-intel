import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

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

export function severityColor(severity: string): string {
  switch (severity) {
    case 'critical': return 'text-danger'
    case 'high': return 'text-warning'
    case 'medium': return 'text-info'
    case 'low': return 'text-text'
    default: return 'text-text'
  }
}
