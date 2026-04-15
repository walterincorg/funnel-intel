import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Play, ExternalLink, CheckCircle, XCircle, Clock, AlertTriangle } from 'lucide-react'
import { api } from '@/api/client'
import { cn, formatDate } from '@/lib/utils'

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
