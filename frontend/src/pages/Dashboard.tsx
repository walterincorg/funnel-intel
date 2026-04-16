import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Play, Clock, CheckCircle, XCircle, AlertTriangle, ArrowRight, Loader } from 'lucide-react'
import { api, type Competitor, type ScanRun } from '@/api/client'
import { cn, formatDate } from '@/lib/utils'

function StatusBadge({ status }: { status: string }) {
  const config = {
    completed: { icon: CheckCircle, color: 'text-success', bg: 'bg-success/10' },
    running: { icon: Clock, color: 'text-info', bg: 'bg-info/10' },
    failed: { icon: XCircle, color: 'text-danger', bg: 'bg-danger/10' },
    pending: { icon: Clock, color: 'text-warning', bg: 'bg-warning/10' },
  }[status] ?? { icon: Clock, color: 'text-text', bg: 'bg-bg-hover' }

  const Icon = config.icon
  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', config.color, config.bg)}>
      <Icon size={12} />
      {status}
    </span>
  )
}

function DriftBadge({ level }: { level: string | null }) {
  if (!level || level === 'none') return null
  const color = level === 'major' ? 'text-danger bg-danger/10' : 'text-warning bg-warning/10'
  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', color)}>
      <AlertTriangle size={12} />
      {level} drift
    </span>
  )
}

function CompetitorCard({ competitor, latestScan, onScan, jobStatus }: {
  competitor: Competitor
  latestScan: ScanRun | undefined
  onScan: () => void
  jobStatus: 'pending' | 'picked' | null
}) {
  const navigate = useNavigate()
  const isActive = jobStatus !== null

  const buttonTitle = jobStatus === 'picked'
    ? 'Scanning…'
    : jobStatus === 'pending'
    ? 'Queued — waiting for worker'
    : 'Trigger scan'

  return (
    <div className="bg-bg-card rounded-xl border border-border p-5 hover:border-accent/30 transition-colors">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3
            className="text-text-bright font-medium cursor-pointer hover:text-accent transition-colors"
            onClick={() => navigate(`/competitors/${competitor.id}`)}
          >
            {competitor.name}
          </h3>
          <p className="text-xs text-text/60 mt-0.5 truncate max-w-[250px]">{competitor.funnel_url}</p>
        </div>
        <button
          onClick={onScan}
          disabled={isActive}
          className={cn(
            'p-2.5 rounded-lg transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center',
            isActive
              ? 'bg-accent/5 text-accent/40 cursor-not-allowed'
              : 'bg-accent/10 text-accent hover:bg-accent/20'
          )}
          title={buttonTitle}
        >
          {isActive
            ? <Loader size={16} className="animate-spin" />
            : <Play size={16} />
          }
        </button>
      </div>

      {latestScan ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <StatusBadge status={latestScan.status} />
            <DriftBadge level={latestScan.drift_level} />
            {latestScan.is_baseline && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-accent-dim text-accent font-medium">baseline</span>
            )}
          </div>
          <div className="flex items-center justify-between text-xs text-text/60">
            <span>{latestScan.total_steps ?? 0} steps</span>
            <span>{formatDate(latestScan.completed_at ?? latestScan.started_at)}</span>
          </div>
          <button
            onClick={() => navigate(`/scans/${latestScan.id}`)}
            className="flex items-center gap-1 text-xs text-accent hover:underline mt-1 min-h-[44px] py-2"
          >
            View scan <ArrowRight size={12} />
          </button>
        </div>
      ) : (
        <p className="text-sm text-text/50 italic">No scans yet</p>
      )}
    </div>
  )
}

export function Dashboard() {
  const queryClient = useQueryClient()

  // Poll active jobs every 3s — source of truth for button state
  const { data: activeJobs } = useQuery({
    queryKey: ['active-jobs'],
    queryFn: api.listActiveJobs,
    refetchInterval: 3000,
  })

  const { data: competitors, isLoading: loadingComp } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  // Poll scans every 5s so completed/failed status appears automatically
  const { data: scans, isLoading: loadingScans } = useQuery({
    queryKey: ['scans'],
    queryFn: () => api.listScans(),
    refetchInterval: 5000,
  })

  // Map: competitor_id → active job status (derived from DB, not local state)
  const activeJobsByCompetitor = new Map<string, 'pending' | 'picked'>()
  for (const job of activeJobs ?? []) {
    activeJobsByCompetitor.set(job.competitor_id, job.status)
  }

  const handleScan = async (competitorId: string) => {
    if (activeJobsByCompetitor.has(competitorId)) return
    try {
      await api.triggerScan(competitorId)
      // Immediately refresh active jobs so spinner appears without waiting for next poll
      queryClient.invalidateQueries({ queryKey: ['active-jobs'] })
    } catch {
      // silent — server error will be reflected on next poll
    }
  }

  if (loadingComp || loadingScans) {
    return <div className="text-text/50 py-12 text-center">Loading...</div>
  }

  // Map: competitor_id → most recent scan
  const latestByCompetitor = new Map<string, ScanRun>()
  for (const scan of scans ?? []) {
    if (!latestByCompetitor.has(scan.competitor_id)) {
      latestByCompetitor.set(scan.competitor_id, scan)
    }
  }

  // Stats
  const totalScans = scans?.length ?? 0
  const activeCompetitors = competitors?.length ?? 0
  const changesDetected = (scans ?? []).filter(s => s.drift_level && s.drift_level !== 'none').length

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-text-bright">Dashboard</h1>
        <p className="text-sm text-text/60 mt-1">Overview of competitor funnel intelligence</p>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <p className="text-xs text-text/60 uppercase tracking-wide">Competitors</p>
          <p className="text-2xl font-semibold text-text-bright mt-1">{activeCompetitors}</p>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <p className="text-xs text-text/60 uppercase tracking-wide">Total Scans</p>
          <p className="text-2xl font-semibold text-text-bright mt-1">{totalScans}</p>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <p className="text-xs text-text/60 uppercase tracking-wide">Changes Detected</p>
          <p className="text-2xl font-semibold text-warning mt-1">{changesDetected}</p>
        </div>
      </div>

      {/* Competitor cards */}
      <h2 className="text-lg font-medium text-text-bright mb-4">Competitors</h2>
      {competitors && competitors.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {competitors.map(comp => (
            <CompetitorCard
              key={comp.id}
              competitor={comp}
              latestScan={latestByCompetitor.get(comp.id)}
              onScan={() => handleScan(comp.id)}
              jobStatus={activeJobsByCompetitor.get(comp.id) ?? null}
            />
          ))}
        </div>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center">
          <p className="text-text/50">No competitors configured yet.</p>
          <p className="text-sm text-text/40 mt-1">Add competitors to start tracking funnels.</p>
        </div>
      )}
    </div>
  )
}
