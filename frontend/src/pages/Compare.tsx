import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type ScanRun, type StepDiff } from '@/api/client'
import { cn, formatDate } from '@/lib/utils'
import { ArrowRight, Plus, Minus, RefreshCw } from 'lucide-react'

function DiffStepRow({ diff }: { diff: StepDiff }) {
  const colors = {
    unchanged: 'border-border',
    changed: 'border-warning/40 bg-warning/5',
    added: 'border-success/40 bg-success/5',
    removed: 'border-danger/40 bg-danger/5',
  }

  const icons = {
    unchanged: null,
    changed: <RefreshCw size={14} className="text-warning" />,
    added: <Plus size={14} className="text-success" />,
    removed: <Minus size={14} className="text-danger" />,
  }

  return (
    <div className={cn('rounded-lg border p-3 mb-2', colors[diff.status])}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-mono text-text/40 w-6">#{diff.step_number}</span>
        {icons[diff.status]}
        <span className={cn(
          'text-xs px-1.5 py-0.5 rounded font-medium',
          diff.status === 'unchanged' ? 'text-text/40' :
          diff.status === 'changed' ? 'text-warning' :
          diff.status === 'added' ? 'text-success' : 'text-danger'
        )}>
          {diff.status}
        </span>
        {diff.changes.length > 0 && (
          <span className="text-xs text-text/40">{diff.changes.join(', ')}</span>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* Run A */}
        <div className="text-xs">
          {diff.run_a ? (
            <>
              <p className="text-text-bright font-medium mb-1">{diff.run_a.question_text ?? '—'}</p>
              {diff.run_a.answer_options && (
                <div className="flex flex-wrap gap-1">
                  {diff.run_a.answer_options.map((o, i) => (
                    <span key={i} className="px-1.5 py-0.5 rounded bg-bg-hover text-text/60 border border-border/50">
                      {o.label}
                    </span>
                  ))}
                </div>
              )}
            </>
          ) : (
            <p className="text-text/30 italic">Not present</p>
          )}
        </div>

        {/* Run B */}
        <div className="text-xs">
          {diff.run_b ? (
            <>
              <p className="text-text-bright font-medium mb-1">{diff.run_b.question_text ?? '—'}</p>
              {diff.run_b.answer_options && (
                <div className="flex flex-wrap gap-1">
                  {diff.run_b.answer_options.map((o, i) => (
                    <span key={i} className="px-1.5 py-0.5 rounded bg-bg-hover text-text/60 border border-border/50">
                      {o.label}
                    </span>
                  ))}
                </div>
              )}
            </>
          ) : (
            <p className="text-text/30 italic">Not present</p>
          )}
        </div>
      </div>
    </div>
  )
}

export function Compare() {
  const [runAId, setRunAId] = useState('')
  const [runBId, setRunBId] = useState('')
  const [selectedCompetitor, setSelectedCompetitor] = useState('')

  const { data: competitors } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  const { data: scans } = useQuery({
    queryKey: ['scans', selectedCompetitor],
    queryFn: () => api.listScans(selectedCompetitor || undefined),
    enabled: !!selectedCompetitor,
  })

  const { data: comparison, isLoading: comparing } = useQuery({
    queryKey: ['compare', runAId, runBId],
    queryFn: () => api.compareRuns(runAId, runBId),
    enabled: !!runAId && !!runBId && runAId !== runBId,
  })

  const selectClass = 'bg-bg-card border border-border rounded-lg px-3 py-2 text-sm text-text-bright focus:outline-none focus:border-accent'

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-text-bright">Compare Runs</h1>
        <p className="text-sm text-text/60 mt-1">Side-by-side comparison of two scan runs</p>
      </div>

      {/* Selector */}
      <div className="bg-bg-card rounded-xl border border-border p-5 mb-6">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 items-end">
          <div>
            <label className="text-xs text-text/60 block mb-1">Competitor</label>
            <select
              value={selectedCompetitor}
              onChange={e => { setSelectedCompetitor(e.target.value); setRunAId(''); setRunBId('') }}
              className={selectClass + ' w-full'}
            >
              <option value="">Select competitor...</option>
              {(competitors ?? []).map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs text-text/60 block mb-1">Run A (older)</label>
            <select value={runAId} onChange={e => setRunAId(e.target.value)} className={selectClass + ' w-full'} disabled={!scans}>
              <option value="">Select run...</option>
              {(scans ?? []).map((s: ScanRun) => (
                <option key={s.id} value={s.id}>
                  {formatDate(s.completed_at ?? s.started_at)} — {s.total_steps ?? 0} steps {s.is_baseline ? '(baseline)' : ''}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs text-text/60 block mb-1">Run B (newer)</label>
            <select value={runBId} onChange={e => setRunBId(e.target.value)} className={selectClass + ' w-full'} disabled={!scans}>
              <option value="">Select run...</option>
              {(scans ?? []).map((s: ScanRun) => (
                <option key={s.id} value={s.id}>
                  {formatDate(s.completed_at ?? s.started_at)} — {s.total_steps ?? 0} steps {s.is_baseline ? '(baseline)' : ''}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Results */}
      {comparing && <div className="text-text/50 py-8 text-center">Comparing...</div>}

      {comparison && (
        <div>
          {/* Summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <div className="bg-bg-card rounded-lg border border-border p-3 text-center">
              <p className="text-xs text-text/50">Steps A</p>
              <p className="text-lg font-semibold text-text-bright">{comparison.total_steps_a}</p>
            </div>
            <div className="bg-bg-card rounded-lg border border-border p-3 text-center">
              <p className="text-xs text-text/50">Steps B</p>
              <p className="text-lg font-semibold text-text-bright">{comparison.total_steps_b}</p>
            </div>
            <div className="bg-bg-card rounded-lg border border-border p-3 text-center">
              <p className="text-xs text-text/50">Changed</p>
              <p className="text-lg font-semibold text-warning">
                {comparison.step_diffs.filter(d => d.status === 'changed').length}
              </p>
            </div>
            <div className="bg-bg-card rounded-lg border border-border p-3 text-center">
              <p className="text-xs text-text/50">Added/Removed</p>
              <p className="text-lg font-semibold text-info">
                {comparison.step_diffs.filter(d => d.status === 'added' || d.status === 'removed').length}
              </p>
            </div>
          </div>

          {/* Column headers */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3 px-10">
            <p className="text-xs text-text/50 font-medium flex items-center gap-1">
              Run A <ArrowRight size={10} />
            </p>
            <p className="text-xs text-text/50 font-medium">Run B</p>
          </div>

          {/* Diff rows */}
          {comparison.step_diffs.map(diff => (
            <DiffStepRow key={diff.step_number} diff={diff} />
          ))}
        </div>
      )}

      {!comparison && !comparing && runAId && runBId && runAId === runBId && (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center text-text/50">
          Select two different runs to compare.
        </div>
      )}
    </div>
  )
}
