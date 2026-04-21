import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Play, ExternalLink, CheckCircle, XCircle, Clock, AlertTriangle, ChevronDown, Sparkles } from 'lucide-react'
import { api, type BuiltWithRelationship } from '@/api/client'
import { cn, formatDate, checkActive, getPrevRunCutoff } from '@/lib/utils'

function RelatedDomainsSection({ rows, prevRunAt }: { rows: BuiltWithRelationship[]; prevRunAt: string | null }) {
  const [inactiveOpen, setInactiveOpen] = useState(false)

  const active = rows.filter(r => checkActive(r.last_detected))
  const inactive = rows.filter(r => !checkActive(r.last_detected))
  const newCount = active.filter(r => !!r.first_seen_at && !!prevRunAt && new Date(r.first_seen_at) > new Date(prevRunAt)).length

  if (rows.length === 0) return (
    <div className="mb-6">
      <h2 className="text-lg font-medium text-text-bright mb-3">Related Domains</h2>
      <div className="bg-bg-card rounded-xl border border-border p-6 text-center text-text/40 text-sm">
        No BuiltWith relationship data yet — run a domain scan.
      </div>
    </div>
  )

  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-lg font-medium text-text-bright">Related Domains</h2>
        {newCount > 0 && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-info/10 text-info">
            <Sparkles size={10} />
            {newCount} new
          </span>
        )}
      </div>
      <div className="bg-bg-card rounded-xl border border-border p-4">
        {active.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50">
                  <th className="text-left py-2 pr-4 text-xs font-medium text-text/50 uppercase tracking-wide">Related domain</th>
                  <th className="text-left py-2 pr-4 text-xs font-medium text-text/50 uppercase tracking-wide">Shared attribute</th>
                  <th className="text-left py-2 pr-4 text-xs font-medium text-text/50 uppercase tracking-wide">First detected</th>
                  <th className="text-left py-2 text-xs font-medium text-text/50 uppercase tracking-wide">Overlap</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {active.map(r => {
                  const fresh = !!r.first_seen_at && !!prevRunAt && new Date(r.first_seen_at) > new Date(prevRunAt)
                  return (
                    <tr key={r.id} className="border-b border-border/30 hover:bg-bg-hover/40">
                      <td className="py-2 pr-4">
                        <a
                          href={`https://${r.related_domain}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 font-mono text-accent hover:underline text-sm"
                        >
                          {r.related_domain}
                          <ExternalLink size={10} />
                        </a>
                      </td>
                      <td className="py-2 pr-4 text-text/70">{r.attribute_value ?? '—'}</td>
                      <td className="py-2 pr-4 text-text/50">{r.first_detected ?? '—'}</td>
                      <td className="py-2 text-text/50">{r.overlap_duration ?? '—'}</td>
                      <td className="py-2 text-right">
                        {fresh && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-xs font-medium bg-info/10 text-info">
                            <Sparkles size={9} /> New
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-text/40 italic">No active relationships.</p>
        )}

        {inactive.length > 0 && (
          <div className="mt-3 border-t border-border/50 pt-2">
            <button
              onClick={() => setInactiveOpen(o => !o)}
              className="flex items-center gap-1.5 text-xs text-text/40 hover:text-text/60 transition-colors"
            >
              <ChevronDown size={12} className={cn('transition-transform', inactiveOpen && 'rotate-180')} />
              {inactive.length} inactive
            </button>
            {inactiveOpen && (
              <table className="w-full mt-3 text-xs text-text/30">
                <tbody>
                  {inactive.map(r => (
                    <tr key={r.id} className="border-b border-border/20">
                      <td className="py-1.5 pr-4 font-mono">{r.related_domain}</td>
                      <td className="py-1.5 pr-4">{r.attribute_value ?? '—'}</td>
                      <td className="py-1.5 pr-4">{r.first_detected ?? '—'}</td>
                      <td className="py-1.5">{r.overlap_duration ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export function CompetitorDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: competitor, isLoading: loadingComp } = useQuery({
    queryKey: ['competitor', id],
    queryFn: () => api.getCompetitor(id!),
    enabled: !!id,
  })

  const { data: scans, isLoading: loadingScans } = useQuery({
    queryKey: ['scans', id],
    queryFn: () => api.listScans(id!),
    enabled: !!id,
    refetchInterval: 5000,
  })

  const { data: activeJobs } = useQuery({
    queryKey: ['active-jobs'],
    queryFn: api.listActiveJobs,
    refetchInterval: 3000,
  })

  const { data: domainRuns } = useQuery({
    queryKey: ['domain-runs'],
    queryFn: api.domainRuns,
  })

  const { data: relatedDomains } = useQuery({
    queryKey: ['bw-relationships', id],
    queryFn: () => api.listRelationships({ competitor_id: id! }),
    enabled: !!id,
  })

  const isScanning = (activeJobs ?? []).some(j => j.competitor_id === id)

  const handleScan = async () => {
    if (!id || isScanning) return
    await api.triggerScan(id)
    queryClient.invalidateQueries({ queryKey: ['active-jobs'] })
  }

  if (loadingComp || loadingScans) {
    return <div className="text-text/50 py-12 text-center">Loading...</div>
  }

  if (!competitor) {
    return <div className="text-text/50 py-12 text-center">Competitor not found</div>
  }

  const statusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle size={16} className="text-success" />
      case 'failed': return <XCircle size={16} className="text-danger" />
      case 'running': return <Clock size={16} className="text-info animate-pulse" />
      default: return <Clock size={16} className="text-warning" />
    }
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
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-text-bright">{competitor.name}</h1>
            <a
              href={competitor.funnel_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-accent hover:underline flex items-center gap-1 mt-1"
            >
              {competitor.funnel_url} <ExternalLink size={12} />
            </a>
          </div>
          <button
            onClick={handleScan}
            disabled={isScanning}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/80 disabled:opacity-50 transition-colors"
          >
            {isScanning ? <Clock size={16} className="animate-pulse" /> : <Play size={16} />}
            {isScanning ? 'Scanning...' : 'Scan Now'}
          </button>
        </div>
      </div>

      {/* Related Domains (BuiltWith) */}
      <RelatedDomainsSection
        rows={relatedDomains ?? []}
        prevRunAt={getPrevRunCutoff(domainRuns ?? [])}
      />

      {/* Scan History */}
      <h2 className="text-lg font-medium text-text-bright mb-4">Scan History</h2>
      {scans && scans.length > 0 ? (
        <div className="space-y-2">
          {scans.map(scan => (
            <div
              key={scan.id}
              onClick={() => navigate(`/scans/${scan.id}`)}
              className="bg-bg-card rounded-lg border border-border p-4 hover:border-accent/30 cursor-pointer transition-colors"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  {statusIcon(scan.status)}
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-text-bright font-medium">
                        {scan.total_steps ?? 0} steps
                      </span>
                      {scan.is_baseline && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-accent-dim text-accent">baseline</span>
                      )}
                      {scan.drift_level && scan.drift_level !== 'none' && (
                        <span className={cn(
                          'text-xs px-1.5 py-0.5 rounded flex items-center gap-1',
                          scan.drift_level === 'major' ? 'bg-danger/10 text-danger' : 'bg-warning/10 text-warning'
                        )}>
                          <AlertTriangle size={10} /> {scan.drift_level} drift
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-text/50 mt-0.5">
                      {scan.stop_reason && `Stopped: ${scan.stop_reason}`}
                    </p>
                  </div>
                </div>
                <span className="text-xs text-text/50">{formatDate(scan.completed_at ?? scan.started_at)}</span>
              </div>
              {scan.drift_details && scan.drift_details.length > 0 && (
                <div className="mt-2 pl-7 space-y-0.5">
                  {scan.drift_details.slice(0, 2).map((d, i) => (
                    <p key={i} className="text-xs text-text/60">{d.description}</p>
                  ))}
                  {scan.drift_details.length > 2 && (
                    <p className="text-xs text-text/40">+{scan.drift_details.length - 2} more</p>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center text-text/50">
          No scans yet. Click "Scan Now" to start.
        </div>
      )}
    </div>
  )
}
