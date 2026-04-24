import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ChevronDown, ChevronRight, MessageSquare, CreditCard, FormInput, Info, Tag, ScrollText, Zap, Wand2, Bot, FileArchive } from 'lucide-react'
import { api, type ScanStep, type ProgressLogEntry } from '@/api/client'
import { cn, formatDate } from '@/lib/utils'

type ReplayMode = 'scripted' | 'patched' | 'full_llm'

interface ReplayCost {
  total_usd: number
  baseline_usd: number
  saved_usd: number
  saved_pct: number
  patches: number
  patch_cost_usd: number
  pricing_extract_usd: number
}

function ModeBadge({ mode, patches }: { mode: ReplayMode; patches: number }) {
  if (mode === 'full_llm') {
    return (
      <span
        className="inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-info/10 text-info font-medium"
        title="Full LLM traversal (no recording yet or escalation)"
      >
        <Bot size={12} /> Full LLM
      </span>
    )
  }
  if (mode === 'patched') {
    return (
      <span
        className="inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-warning/10 text-warning font-medium"
        title="Scripted replay with one or more single-step LLM patches on the shared browser"
      >
        <Wand2 size={12} /> Scripted · {patches} LLM patch{patches === 1 ? '' : 'es'}
      </span>
    )
  }
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-success/10 text-success font-medium"
      title="Deterministic Playwright replay — zero LLM calls"
    >
      <Zap size={12} /> Scripted
    </span>
  )
}

function StepModeTag({ mode }: { mode?: 'scripted' | 'patched' }) {
  if (!mode) return null
  if (mode === 'patched') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-warning/10 text-warning border border-warning/30">
        <Wand2 size={10} /> patched
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-success/10 text-success border border-success/30">
      <Zap size={10} /> scripted
    </span>
  )
}

function StepIcon({ type }: { type: string }) {
  switch (type) {
    case 'question': return <MessageSquare size={14} className="text-accent" />
    case 'pricing': return <CreditCard size={14} className="text-success" />
    case 'discount': return <Tag size={14} className="text-warning" />
    case 'input': return <FormInput size={14} className="text-info" />
    default: return <Info size={14} className="text-text/50" />
  }
}

function LogTypeIcon({ type }: { type: string }) {
  switch (type) {
    case 'question': return <span className="text-accent">Q</span>
    case 'pricing': return <span className="text-success">$</span>
    case 'discount': return <span className="text-warning">%</span>
    case 'input': return <span className="text-info">F</span>
    default: return <span className="text-text/40">·</span>
  }
}

function StepRow({ step }: { step: ScanStep }) {
  const [expanded, setExpanded] = useState(false)
  const replayMode = step.metadata?.replay_mode as 'scripted' | 'patched' | undefined

  return (
    <div className={cn(
      'border-b border-border last:border-b-0',
      replayMode === 'patched' && 'bg-warning/[0.04]'
    )}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-bg-hover/50 transition-colors"
      >
        <span className="shrink-0 w-7 text-center text-xs font-mono text-text/40">
          {step.step_number}
        </span>
        <span className="shrink-0">
          <StepIcon type={step.step_type} />
        </span>
        <span className="text-sm text-text-bright truncate flex-1 min-w-0">
          {step.question_text || <span className="text-text/40 italic">{step.step_type} screen</span>}
        </span>
        {step.action_taken && (
          <span className="shrink-0 text-xs text-accent/70 max-w-[200px] truncate hidden sm:block">
            {step.action_taken}
          </span>
        )}
        <StepModeTag mode={replayMode} />
        <ChevronRight size={14} className={cn(
          'shrink-0 text-text/30 transition-transform',
          expanded && 'rotate-90'
        )} />
      </button>

      {expanded && (
        <div className="px-4 pb-3 pl-14 space-y-2">
          {step.answer_options && step.answer_options.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {step.answer_options.map((opt, i) => (
                <span
                  key={i}
                  className={cn(
                    'text-xs px-2 py-0.5 rounded-full border',
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
            <p className="text-xs text-accent/80">Action: {step.action_taken}</p>
          )}
          {step.url && (
            <p className="text-xs text-text/40 truncate">{step.url}</p>
          )}
        </div>
      )}
    </div>
  )
}

export function ScanDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [showAllChanges, setShowAllChanges] = useState(false)
  const [showProgressLog, setShowProgressLog] = useState(false)

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

  const { data: recording } = useQuery({
    queryKey: ['recording', scan?.competitor_id],
    queryFn: () => api.getCompetitorRecording(scan!.competitor_id),
    enabled: !!scan?.competitor_id,
    retry: false,
  })

  if (loadingScan || loadingSteps) {
    return <div className="text-text/50 py-12 text-center">Loading...</div>
  }

  if (!scan) {
    return <div className="text-text/50 py-12 text-center">Scan not found</div>
  }

  const summary = (scan.summary ?? {}) as Record<string, unknown>
  const driftSummary = summary.drift_summary as string | undefined
  const mode = ((summary.mode as ReplayMode | undefined) ??
    (recording ? 'scripted' : 'full_llm')) as ReplayMode
  const patchCount = (summary.patch_count as number | undefined) ?? 0
  const cost = summary.cost as ReplayCost | undefined
  const progressLog = scan.progress_log ?? []
  const patchedSteps = (steps ?? []).filter(s => s.metadata?.replay_mode === 'patched')

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
              {scan.status === 'completed' && <ModeBadge mode={mode} patches={patchCount} />}
            </div>
          </div>
          <div className="text-right text-xs text-text/50 space-y-1">
            <p>Started: {formatDate(scan.started_at)}</p>
            <p>Completed: {formatDate(scan.completed_at)}</p>
            <p>{scan.total_steps ?? 0} steps &middot; {scan.stop_reason ?? 'unknown'}</p>
          </div>
        </div>

        {/* Drift summary (LLM-generated) */}
        {driftSummary && (
          <p className="mt-4 text-sm text-text/80 leading-relaxed">{driftSummary}</p>
        )}

        {/* Drift details — collapsed by default */}
        {scan.drift_details && scan.drift_details.length > 0 && (
          <div className="mt-3">
            <button
              onClick={() => setShowAllChanges(!showAllChanges)}
              className="flex items-center gap-1.5 text-xs text-text/50 hover:text-text/70 transition-colors"
            >
              {showAllChanges ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              {scan.drift_details.length} change{scan.drift_details.length !== 1 ? 's' : ''} detected
            </button>
            {showAllChanges && (
              <div className="mt-2 p-3 bg-bg/50 rounded-lg border border-border space-y-1">
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
            )}
          </div>
        )}
      </div>

      {/* Replay summary cards — cost, drift, recording. Only shown when we
          have signal (i.e. a completed run or an existing recording). */}
      {scan.status === 'completed' && (cost || recording) && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          {cost && (
            <div className="bg-bg-card rounded-xl border border-success/30 p-5">
              <div className="text-xs uppercase tracking-wide text-text/50 mb-2">Cost this run</div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-semibold text-text-bright">
                  ${cost.total_usd.toFixed(2)}
                </span>
                <span className="text-sm text-text/40 line-through">
                  ${cost.baseline_usd.toFixed(2)}
                </span>
              </div>
              <div className="mt-1 text-xs text-success">
                {cost.saved_pct.toFixed(1)}% saved vs full LLM
              </div>
              <div className="mt-2 text-xs text-text/50">
                {patchCount > 0 && (
                  <>{patchCount} patch{patchCount === 1 ? '' : 'es'} (${cost.patch_cost_usd.toFixed(2)}){cost.pricing_extract_usd > 0 && ' · '}</>
                )}
                {cost.pricing_extract_usd > 0 && <>1 Haiku pricing pass (${cost.pricing_extract_usd.toFixed(2)})</>}
              </div>
            </div>
          )}

          <div className="bg-bg-card rounded-xl border border-border p-5">
            <div className="text-xs uppercase tracking-wide text-text/50 mb-2">Drift</div>
            <div className="text-2xl font-semibold text-text-bright capitalize">
              {scan.drift_level || 'none'}
            </div>
            <div className="mt-2 text-xs text-text/50">
              {scan.drift_details?.length
                ? `${scan.drift_details.length} change${scan.drift_details.length === 1 ? '' : 's'} detected`
                : 'No changes detected'}
            </div>
          </div>

          {recording && (
            <div className={cn(
              'bg-bg-card rounded-xl border p-5',
              recording.is_stale ? 'border-danger/30' : 'border-border'
            )}>
              <div className="text-xs uppercase tracking-wide text-text/50 mb-2">Recording</div>
              <div className="text-2xl font-semibold text-text-bright">
                {recording.is_stale ? 'Stale' : recording.patch_count > 0 ? 'Patched' : 'Healthy'}
              </div>
              <div className="mt-2 text-xs text-text/50">
                Frozen {formatDate(recording.captured_at)} · {recording.patch_count} total patch{recording.patch_count === 1 ? '' : 'es'}
              </div>
              {recording.trace_url && (
                <a
                  href={recording.trace_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-flex items-center gap-1.5 text-xs text-accent hover:underline"
                >
                  <FileArchive size={12} /> trace.zip
                </a>
              )}
            </div>
          )}
        </div>
      )}

      {/* Patch callout — surfaces what happened on the scripted path when at
          least one step was rescued by the LLM this run. */}
      {patchedSteps.length > 0 && (
        <div className="bg-warning/[0.06] border border-warning/30 rounded-xl p-4 mb-6">
          <div className="flex items-start gap-2.5">
            <Wand2 size={16} className="text-warning shrink-0 mt-0.5" />
            <div className="text-sm text-text/80">
              <p>
                <strong className="text-text-bright">
                  {patchedSteps.length} LLM patch{patchedSteps.length === 1 ? '' : 'es'} applied.
                </strong>{' '}
                Scripted replay hit selector timeouts on step{patchedSteps.length === 1 ? '' : 's'}{' '}
                {patchedSteps.map(s => s.step_number).join(', ')}. The shared browser was handed to a single-step
                browser-use Agent to recover, then scripted playback resumed.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Progress log — collapsed by default */}
      {progressLog.length > 0 && (
        <div className="bg-bg-card rounded-xl border border-border mb-6">
          <button
            onClick={() => setShowProgressLog(!showProgressLog)}
            className="w-full flex items-center gap-2 p-4 text-left hover:bg-bg-hover/30 transition-colors rounded-xl"
          >
            <ScrollText size={16} className="text-accent shrink-0" />
            <span className="text-sm font-semibold text-text-bright">Progress Log</span>
            <span className="text-xs text-text/40">{progressLog.length} events</span>
            <span className="ml-auto">
              {showProgressLog ? <ChevronDown size={14} className="text-text/30" /> : <ChevronRight size={14} className="text-text/30" />}
            </span>
          </button>
          {showProgressLog && (
            <div className="px-5 pb-4 space-y-1.5 max-h-[400px] overflow-y-auto">
              {progressLog.map((entry: ProgressLogEntry, i: number) => (
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
          )}
        </div>
      )}

      {/* Steps — compact table */}
      <h2 className="text-lg font-medium text-text-bright mb-3">
        Steps
        {steps && <span className="text-sm font-normal text-text/40 ml-2">{steps.length}</span>}
      </h2>
      {steps && steps.length > 0 ? (
        <div className="bg-bg-card rounded-xl border border-border overflow-hidden">
          {steps.map(step => (
            <StepRow key={step.id} step={step} />
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
