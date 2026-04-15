import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Rocket,
  AlertTriangle,
  Clock,
  CheckCircle2,
  XCircle,
  CircleDashed,
  Zap,
  Play,
  ChevronDown,
} from 'lucide-react'
import { api, type ShipListItem, type ShipListResponse, type SynthesisRun } from '@/api/client'
import { cn } from '@/lib/utils'

// ---------- Helpers ----------

const EFFORT_LABELS: Record<ShipListItem['effort_estimate'], string> = {
  XS: '< 1 hour',
  S: 'half day',
  M: '1-2 days',
  L: '1+ week',
}

const STATUS_CONFIG: Record<ShipListItem['status'], { label: string; color: string }> = {
  proposed: { label: 'Proposed', color: 'bg-info/10 text-info' },
  shipping: { label: 'Shipping', color: 'bg-warning/10 text-warning' },
  shipped: { label: 'Shipped', color: 'bg-success/10 text-success' },
  skipped: { label: 'Skipped', color: 'bg-bg-hover text-text/50' },
  expired: { label: 'Expired', color: 'bg-bg-hover text-text/50' },
}

function formatWeekOf(week: string): string {
  // "2026-04-13" -> "Week of April 13, 2026"
  const [y, m, d] = week.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  return `Week of ${date.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  })}`
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, (value / 10) * 100))
  const color =
    value >= 8 ? 'bg-success' : value >= 6 ? 'bg-info' : 'bg-warning'
  return (
    <div className="flex items-center gap-2">
      <div className="w-24 h-1.5 bg-bg-hover rounded-full overflow-hidden">
        <div className={cn('h-full', color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-text/70 tabular-nums">{value.toFixed(1)}/10</span>
    </div>
  )
}

// ---------- Page states ----------

function LoadingState() {
  return (
    <div className="max-w-3xl mx-auto py-16 text-center">
      <Clock className="mx-auto mb-4 text-text/40 animate-pulse" size={36} />
      <p className="text-text/60">Loading this week's ship list...</p>
    </div>
  )
}

function EmptyState({ run, onTrigger }: { run: SynthesisRun | null; onTrigger: () => void }) {
  return (
    <div className="max-w-3xl mx-auto py-16 text-center">
      <CircleDashed className="mx-auto mb-6 text-text/40" size={48} />
      <h2 className="text-2xl font-semibold text-text-bright mb-3">
        No strong signal this week
      </h2>
      <p className="text-text/60 mb-2 leading-relaxed">
        The synthesis pipeline ran but no patterns rose to the confidence bar. Empty
        is honest. Weak recommendations are worse than none.
      </p>
      {run && (
        <p className="text-xs text-text/40 mb-8">
          {run.patterns_found ?? 0} patterns mined, {run.candidate_pattern_count ?? 0} eligible,
          none strong enough to ship.
        </p>
      )}
      <button
        onClick={onTrigger}
        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-dim text-accent hover:bg-accent hover:text-white transition-colors text-sm"
      >
        <Play size={14} />
        Re-run synthesis
      </button>
    </div>
  )
}

function StaleBanner({ count, run }: { count: number; run: SynthesisRun | null }) {
  const sources = (run?.stale_sources ?? []) as Array<Record<string, unknown>>
  return (
    <div className="max-w-3xl mx-auto mb-6 p-4 rounded-lg border border-warning/30 bg-warning/5 flex items-start gap-3">
      <AlertTriangle size={18} className="text-warning mt-0.5 flex-shrink-0" />
      <div className="flex-1 text-sm">
        <div className="text-warning font-medium mb-1">
          {count} data source{count === 1 ? '' : 's'} stale
        </div>
        <div className="text-text/70">
          One or more tracked sources hasn't succeeded recently. Recommendations that
          depend on these sources are marked{' '}
          <span className="font-mono text-warning">[stale]</span>. Fix the upstream
          pipeline before acting on anything flagged.
        </div>
        {sources.length > 0 && (
          <div className="mt-2 text-xs text-text/50">
            Sources:{' '}
            {sources
              .map((s) => String(s.source ?? 'unknown'))
              .filter((v, i, a) => a.indexOf(v) === i)
              .join(', ')}
          </div>
        )}
      </div>
    </div>
  )
}

function ErrorState({ run }: { run: SynthesisRun | null }) {
  return (
    <div className="max-w-3xl mx-auto py-16 text-center">
      <XCircle className="mx-auto mb-4 text-danger" size={36} />
      <h2 className="text-xl font-semibold text-text-bright mb-2">
        Synthesis failed
      </h2>
      <p className="text-text/60 max-w-md mx-auto mb-4 text-sm">
        The ship list generator returned an error. See below for details.
      </p>
      {run?.error && (
        <pre className="inline-block text-xs text-danger bg-danger/5 border border-danger/20 rounded-lg px-4 py-3 max-w-xl text-left whitespace-pre-wrap">
          {run.error}
        </pre>
      )}
    </div>
  )
}

// ---------- Item card ----------

function ShipListItemCard({
  item,
  index,
  onStatusChange,
}: {
  item: ShipListItem
  index: number
  onStatusChange: (status: ShipListItem['status']) => void
}) {
  const [expanded, setExpanded] = useState(index === 0)
  const statusConfig = STATUS_CONFIG[item.status]

  return (
    <article className="bg-bg-card border border-border rounded-xl overflow-hidden">
      {/* Header */}
      <header className="p-5 border-b border-border">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-8 h-8 rounded-full bg-accent-dim text-accent flex items-center justify-center text-sm font-semibold flex-shrink-0">
              {item.rank}
            </div>
            <div className="min-w-0">
              <h3 className="text-lg font-semibold text-text-bright leading-snug">
                {item.headline}
              </h3>
            </div>
          </div>
          <span
            className={cn(
              'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium flex-shrink-0',
              statusConfig.color,
            )}
          >
            {statusConfig.label}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-text/60">
          <div className="flex items-center gap-1.5">
            <Zap size={12} />
            <span>
              Effort:{' '}
              <span className="text-text font-medium">
                {item.effort_estimate} · {EFFORT_LABELS[item.effort_estimate]}
              </span>
            </span>
          </div>
          <ConfidenceBar value={item.confidence} />
          <div className="text-text/50">
            {item.pattern_ids.length} citation
            {item.pattern_ids.length === 1 ? '' : 's'}
          </div>
        </div>
      </header>

      {/* Body */}
      <div className="p-5 space-y-4">
        <div>
          <div className="text-xs uppercase tracking-wide text-text/40 mb-1.5">
            Recommendation
          </div>
          <p className="text-text leading-relaxed whitespace-pre-wrap">
            {item.recommendation}
          </p>
        </div>

        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-xs text-text/50 hover:text-text transition-colors"
        >
          <ChevronDown
            size={14}
            className={cn('transition-transform', expanded && 'rotate-180')}
          />
          {expanded ? 'Hide' : 'Show'} test plan
        </button>

        {expanded && (
          <div className="space-y-4 pt-2 border-t border-border">
            <div>
              <div className="text-xs uppercase tracking-wide text-text/40 mb-1.5">
                Test plan
              </div>
              <p className="text-text/80 text-sm leading-relaxed whitespace-pre-wrap">
                {item.test_plan}
              </p>
            </div>

            {item.swipe_file_refs && item.swipe_file_refs.length > 0 && (
              <div>
                <div className="text-xs uppercase tracking-wide text-text/40 mb-1.5">
                  Swipe file
                </div>
                <div className="flex flex-wrap gap-2">
                  {item.swipe_file_refs.map((ref, i) => (
                    <span
                      key={`${ref.id}-${i}`}
                      className="inline-flex items-center gap-1.5 px-2 py-1 rounded bg-bg-hover text-xs text-text/70"
                    >
                      <span className="font-mono text-text/40">{ref.type}</span>
                      {ref.label || ref.id.slice(0, 8)}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div>
              <div className="text-xs uppercase tracking-wide text-text/40 mb-1.5">
                Evidence
              </div>
              <div className="flex flex-wrap gap-1.5">
                {item.pattern_ids.map((pid) => (
                  <span
                    key={pid}
                    className="inline-block px-2 py-0.5 rounded bg-bg-hover font-mono text-xs text-text/50"
                    title={pid}
                  >
                    {pid.slice(0, 8)}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Footer actions */}
      <footer className="px-5 py-3 bg-bg border-t border-border flex items-center justify-between">
        <div className="text-xs text-text/40">
          {item.status === 'shipped' && item.shipped_at
            ? `Shipped ${new Date(item.shipped_at).toLocaleDateString()}`
            : 'Ready for action'}
        </div>
        <div className="flex items-center gap-2">
          {item.status === 'proposed' && (
            <>
              <button
                onClick={() => onStatusChange('shipping')}
                className="px-3 py-1.5 rounded-lg bg-accent text-white text-xs font-medium hover:bg-accent-bright transition-colors inline-flex items-center gap-1.5"
              >
                <Rocket size={12} />
                Shipping this
              </button>
              <button
                onClick={() => onStatusChange('skipped')}
                className="px-3 py-1.5 rounded-lg text-text/50 text-xs hover:bg-bg-hover transition-colors"
              >
                Skip
              </button>
            </>
          )}
          {item.status === 'shipping' && (
            <>
              <button
                onClick={() => onStatusChange('shipped')}
                className="px-3 py-1.5 rounded-lg bg-success text-white text-xs font-medium hover:opacity-90 transition-opacity inline-flex items-center gap-1.5"
              >
                <CheckCircle2 size={12} />
                Mark shipped
              </button>
              <button
                onClick={() => onStatusChange('proposed')}
                className="px-3 py-1.5 rounded-lg text-text/50 text-xs hover:bg-bg-hover transition-colors"
              >
                Undo
              </button>
            </>
          )}
          {(item.status === 'shipped' || item.status === 'skipped') && (
            <button
              onClick={() => onStatusChange('proposed')}
              className="px-3 py-1.5 rounded-lg text-text/50 text-xs hover:bg-bg-hover transition-colors"
            >
              Reopen
            </button>
          )}
        </div>
      </footer>
    </article>
  )
}

// ---------- Page ----------

export function ShipList() {
  const qc = useQueryClient()
  const [selectedWeek, setSelectedWeek] = useState<string | null>(null)

  const { data, isLoading } = useQuery<ShipListResponse>({
    queryKey: ['ship-list', selectedWeek],
    queryFn: () => api.getShipList(selectedWeek ?? undefined),
  })

  const triggerMut = useMutation({
    mutationFn: () => api.triggerSynthesis(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ship-list'] }),
  })

  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ShipListItem['status'] }) =>
      api.updateShipItemStatus(id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ship-list'] }),
  })

  if (isLoading || !data) return <LoadingState />

  const { items, run, available_weeks, is_stale, week_of } = data
  const runStatus = run?.status ?? null

  return (
    <div className="px-6 py-8">
      {/* Header */}
      <header className="max-w-3xl mx-auto mb-8">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h1 className="text-3xl font-semibold text-text-bright mb-1 flex items-center gap-3">
              <Rocket size={28} className="text-accent" />
              Ship List
            </h1>
            <p className="text-text/60 text-sm">
              {week_of
                ? formatWeekOf(week_of)
                : 'The weekly directive from your competitive intelligence'}
            </p>
          </div>

          {available_weeks.length > 1 && (
            <select
              value={selectedWeek ?? week_of ?? ''}
              onChange={(e) => setSelectedWeek(e.target.value || null)}
              className="text-sm bg-bg-card border border-border rounded-lg px-3 py-2 text-text"
            >
              {available_weeks.map((w) => (
                <option key={w.week_of} value={w.week_of}>
                  {formatWeekOf(w.week_of)} ({w.item_count})
                </option>
              ))}
            </select>
          )}
        </div>

        {run && runStatus === 'completed' && (
          <div className="text-xs text-text/40 flex flex-wrap items-center gap-x-4 gap-y-1">
            <span>Generated {new Date(run.created_at).toLocaleString()}</span>
            <span>·</span>
            <span>{run.patterns_persisted ?? 0} patterns mined</span>
            <span>·</span>
            <span>{run.llm_cost_cents ?? 0}¢ LLM cost</span>
            {run.duration_s != null && (
              <>
                <span>·</span>
                <span>{run.duration_s}s</span>
              </>
            )}
            {(run.items_rejected_citation ?? 0) > 0 && (
              <>
                <span>·</span>
                <span className="text-warning">
                  {run.items_rejected_citation} citation rejects
                </span>
              </>
            )}
          </div>
        )}
      </header>

      {/* Stale banner */}
      {is_stale && <StaleBanner count={data.stale_source_count} run={run} />}

      {/* Content */}
      {runStatus === 'failed' && <ErrorState run={run} />}

      {runStatus === 'aborted_stale' && (
        <div className="max-w-3xl mx-auto py-16 text-center">
          <AlertTriangle size={48} className="mx-auto mb-4 text-warning" />
          <h2 className="text-xl font-semibold text-text-bright mb-2">
            Synthesis aborted: stale data
          </h2>
          <p className="text-text/60 text-sm max-w-md mx-auto">
            The synthesis loop refused to generate a ship list because one or more
            upstream sources were beyond the freshness threshold. Fix those first,
            then re-run.
          </p>
        </div>
      )}

      {runStatus !== 'failed' && runStatus !== 'aborted_stale' && items.length === 0 && (
        <EmptyState run={run} onTrigger={() => triggerMut.mutate()} />
      )}

      {items.length > 0 && (
        <div className="max-w-3xl mx-auto space-y-5">
          {items.map((item, i) => (
            <ShipListItemCard
              key={item.id}
              item={item}
              index={i}
              onStatusChange={(status) => statusMut.mutate({ id: item.id, status })}
            />
          ))}
        </div>
      )}
    </div>
  )
}
