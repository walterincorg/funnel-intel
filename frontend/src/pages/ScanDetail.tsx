import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, MessageSquare, CreditCard, FormInput, Info, Tag, ScrollText } from 'lucide-react'
import { api, type ScanStep, type ProgressLogEntry } from '@/api/client'
import { cn, formatDate } from '@/lib/utils'

function LogTypeIcon({ type }: { type: string }) {
  switch (type) {
    case 'question': return <span className="text-accent">Q</span>
    case 'pricing': return <span className="text-success">$</span>
    case 'discount': return <span className="text-warning">%</span>
    case 'input': return <span className="text-info">F</span>
    default: return <span className="text-text/40">·</span>
  }
}

function ProgressLog({ entries }: { entries: ProgressLogEntry[] }) {
  if (!entries.length) return null
  return (
    <div className="bg-bg-card rounded-xl border border-border p-5 mb-6">
      <div className="flex items-center gap-2 mb-3">
        <ScrollText size={16} className="text-accent" />
        <h2 className="text-sm font-semibold text-text-bright">Progress Log</h2>
        <span className="text-xs text-text/40">{entries.length} events</span>
      </div>
      <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
        {entries.map((entry, i) => (
          <div key={i} className="flex gap-2.5 text-sm leading-relaxed">
            <span className="shrink-0 w-8 text-right text-xs text-text/30 pt-0.5 font-mono">
              {entry.step}
            </span>
            <span className="shrink-0 w-4 text-center text-xs pt-0.5 font-medium">
              <LogTypeIcon type={entry.type} />
            </span>
            <span className="text-text/80">{entry.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function StepIcon({ type }: { type: string }) {
  switch (type) {
    case 'question': return <MessageSquare size={16} className="text-accent" />
    case 'pricing': return <CreditCard size={16} className="text-success" />
    case 'discount': return <Tag size={16} className="text-warning" />
    case 'input': return <FormInput size={16} className="text-info" />
    default: return <Info size={16} className="text-text/50" />
  }
}

function StepCard({ step }: { step: ScanStep }) {
  return (
    <div className="flex gap-4">
      {/* Timeline line */}
      <div className="flex flex-col items-center">
        <div className="w-8 h-8 rounded-full bg-bg-card border border-border flex items-center justify-center text-xs font-medium text-text/60 shrink-0">
          {step.step_number}
        </div>
        <div className="w-px flex-1 bg-border" />
      </div>

      {/* Step content */}
      <div className="bg-bg-card rounded-lg border border-border p-4 mb-3 flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-2">
          <StepIcon type={step.step_type} />
          <span className="text-xs px-2 py-0.5 rounded-full bg-bg-hover text-text/70 font-medium uppercase">
            {step.step_type}
          </span>
          {step.url && (
            <span className="text-xs text-text/40 truncate ml-auto max-w-[200px]">{step.url}</span>
          )}
        </div>

        {step.question_text && (
          <p className="text-sm text-text-bright font-medium mb-2">{step.question_text}</p>
        )}

        {step.answer_options && step.answer_options.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2">
            {step.answer_options.map((opt, i) => (
              <span
                key={i}
                className={cn(
                  'text-xs px-2.5 py-1 rounded-full border',
                  step.action_taken?.includes(opt.label)
                    ? 'border-accent bg-accent-dim text-accent'
                    : 'border-border text-text/60'
                )}
              >
                {opt.label}
              </span>
            ))}
          </div>
        )}

        {step.action_taken && (
          <p className="text-xs text-accent/80">
            Action: {step.action_taken}
          </p>
        )}
      </div>
    </div>
  )
}

export function ScanDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const { data: scan, isLoading: loadingScan } = useQuery({
    queryKey: ['scan', id],
    queryFn: () => api.getScan(id!),
    enabled: !!id,
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 5000 : false,
  })

  const { data: steps, isLoading: loadingSteps } = useQuery({
    queryKey: ['scanSteps', id],
    queryFn: () => api.getScanSteps(id!),
    enabled: !!id,
  })

  if (loadingScan || loadingSteps) {
    return <div className="text-text/50 py-12 text-center">Loading...</div>
  }

  if (!scan) {
    return <div className="text-text/50 py-12 text-center">Scan not found</div>
  }

  return (
    <div>
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-1.5 text-sm text-text/60 hover:text-text-bright mb-4 transition-colors"
      >
        <ArrowLeft size={16} /> Back
      </button>

      {/* Header */}
      <div className="bg-bg-card rounded-xl border border-border p-6 mb-6">
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <h1 className="text-xl font-semibold text-text-bright">Scan Detail</h1>
            <div className="flex items-center gap-3 mt-2 flex-wrap">
              <span className={cn(
                'text-xs px-2 py-0.5 rounded-full font-medium',
                scan.status === 'completed' ? 'bg-success/10 text-success' :
                scan.status === 'failed' ? 'bg-danger/10 text-danger' :
                'bg-info/10 text-info'
              )}>
                {scan.status}
              </span>
              {scan.is_baseline && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-accent-dim text-accent font-medium">baseline</span>
              )}
              {scan.drift_level && scan.drift_level !== 'none' && (
                <span className={cn(
                  'text-xs px-2 py-0.5 rounded-full font-medium',
                  scan.drift_level === 'major' ? 'bg-danger/10 text-danger' : 'bg-warning/10 text-warning'
                )}>
                  {scan.drift_level} drift
                </span>
              )}
            </div>
          </div>
          <div className="text-right text-xs text-text/50 space-y-1">
            <p>Started: {formatDate(scan.started_at)}</p>
            <p>Completed: {formatDate(scan.completed_at)}</p>
            <p>{scan.total_steps ?? 0} steps &middot; {scan.stop_reason ?? 'unknown'}</p>
          </div>
        </div>

        {/* Drift details */}
        {scan.drift_details && scan.drift_details.length > 0 && (
          <div className="mt-4 p-3 bg-bg/50 rounded-lg border border-warning/20">
            <p className="text-xs font-medium text-warning mb-2">Changes Detected</p>
            <div className="space-y-1">
              {scan.drift_details.map((d, i) => (
                <p key={i} className="text-xs text-text/70">
                  <span className={cn(
                    'inline-block w-1.5 h-1.5 rounded-full mr-1.5',
                    d.severity === 'critical' ? 'bg-danger' :
                    d.severity === 'high' ? 'bg-warning' :
                    d.severity === 'medium' ? 'bg-info' : 'bg-text/30'
                  )} />
                  {d.description}
                </p>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Progress log */}
      {scan.progress_log && scan.progress_log.length > 0 && (
        <ProgressLog entries={scan.progress_log} />
      )}

      {/* Steps timeline */}
      <h2 className="text-lg font-medium text-text-bright mb-4">Steps</h2>
      {steps && steps.length > 0 ? (
        <div>
          {steps.map(step => (
            <StepCard key={step.id} step={step} />
          ))}
        </div>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center text-text/50">
          No steps recorded
        </div>
      )}
    </div>
  )
}
