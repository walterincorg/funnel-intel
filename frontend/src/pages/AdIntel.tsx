import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Megaphone, Play, TrendingUp, Trophy, Sparkles, ArrowRightLeft, X, Zap } from 'lucide-react'
import { api, type Competitor, type AdSignal } from '@/api/client'
import { cn, formatDate } from '@/lib/utils'

const SIGNAL_CONFIG: Record<string, { label: string; icon: typeof Megaphone; color: string; bg: string }> = {
  new_ad: { label: 'New Ad', icon: Sparkles, color: 'text-info', bg: 'bg-info/10' },
  proven_winner: { label: 'Winner', icon: Trophy, color: 'text-success', bg: 'bg-success/10' },
  count_spike: { label: 'Spike', icon: TrendingUp, color: 'text-danger', bg: 'bg-danger/10' },
  copy_change: { label: 'Copy Change', icon: ArrowRightLeft, color: 'text-warning', bg: 'bg-warning/10' },
  platform_expansion: { label: 'Expand', icon: Megaphone, color: 'text-accent', bg: 'bg-accent/10' },
  failed_test: { label: 'Failed', icon: X, color: 'text-danger', bg: 'bg-danger/10' },
}

const SEVERITY_COLOR: Record<string, string> = {
  high: 'bg-danger/20 text-danger',
  medium: 'bg-warning/20 text-warning',
  low: 'bg-info/20 text-info',
}

function SignalCard({ signal, competitorName }: { signal: AdSignal; competitorName: string }) {
  const config = SIGNAL_CONFIG[signal.signal_type] ?? SIGNAL_CONFIG.new_ad
  const Icon = config.icon

  return (
    <div className="bg-bg-card rounded-xl border border-border p-4 hover:border-accent/30 transition-colors">
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', config.color, config.bg)}>
            <Icon size={12} />
            {config.label}
          </span>
          <span className={cn('px-2 py-0.5 rounded-full text-xs font-medium', SEVERITY_COLOR[signal.severity] ?? SEVERITY_COLOR.medium)}>
            {signal.severity}
          </span>
        </div>
        <span className="text-xs text-text/50">{signal.signal_date}</span>
      </div>
      <p className="text-sm text-text-bright font-medium">{signal.title}</p>
      {signal.detail && (
        <p className="text-xs text-text/60 mt-1 line-clamp-2">{signal.detail}</p>
      )}
      <p className="text-xs text-text/40 mt-2">{competitorName}</p>
    </div>
  )
}

export function AdIntel() {
  const queryClient = useQueryClient()
  const [filterCompetitor, setFilterCompetitor] = useState<string>('')
  const [filterType, setFilterType] = useState<string>('')
  const [days, setDays] = useState(7)

  const { data: competitors } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  const { data: signals, isLoading } = useQuery({
    queryKey: ['ad-signals', filterCompetitor, filterType, days],
    queryFn: () => api.listAdSignals({
      competitor_id: filterCompetitor || undefined,
      signal_type: filterType || undefined,
      days,
    }),
  })

  const { data: summary } = useQuery({
    queryKey: ['ad-signals-summary', days],
    queryFn: () => api.adSignalsSummary(days),
  })

  const scrapeMutation = useMutation({
    mutationFn: api.triggerAdScrape,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ad-signals'] })
      queryClient.invalidateQueries({ queryKey: ['ad-signals-summary'] })
    },
  })

  // Build competitor name lookup
  const compMap = new Map<string, string>()
  for (const c of competitors ?? []) {
    compMap.set(c.id, c.name)
  }

  // Stats from summary
  const summaryMap = new Map<string, number>()
  for (const s of summary ?? []) {
    summaryMap.set(s.signal_type, s.count)
  }
  const newAds = summaryMap.get('new_ad') ?? 0
  const totalSignals = (summary ?? []).reduce((acc, s) => acc + s.count, 0)
  const provenWinners = summaryMap.get('proven_winner') ?? 0

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-text-bright flex items-center gap-2">
            <Megaphone size={24} className="text-accent" />
            Ad Intelligence
          </h1>
          <p className="text-sm text-text/60 mt-1">What competitors did differently — and does it matter?</p>
        </div>
        <button
          onClick={() => scrapeMutation.mutate()}
          disabled={scrapeMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
        >
          <Play size={16} />
          {scrapeMutation.isPending ? 'Scraping...' : 'Scrape Now'}
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <p className="text-xs text-text/60 uppercase tracking-wide">New Ads ({days}d)</p>
          <p className="text-2xl font-semibold text-info mt-1">{newAds}</p>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <p className="text-xs text-text/60 uppercase tracking-wide">Total Signals ({days}d)</p>
          <p className="text-2xl font-semibold text-text-bright mt-1">{totalSignals}</p>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <p className="text-xs text-text/60 uppercase tracking-wide">Proven Winners</p>
          <p className="text-2xl font-semibold text-success mt-1">{provenWinners}</p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <select
          value={filterCompetitor}
          onChange={e => setFilterCompetitor(e.target.value)}
          className="bg-bg-card border border-border rounded-lg px-3 py-1.5 text-sm text-text-bright"
        >
          <option value="">All Competitors</option>
          {(competitors ?? []).map(c => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>

        <select
          value={filterType}
          onChange={e => setFilterType(e.target.value)}
          className="bg-bg-card border border-border rounded-lg px-3 py-1.5 text-sm text-text-bright"
        >
          <option value="">All Signals</option>
          {Object.entries(SIGNAL_CONFIG).map(([key, { label }]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>

        <div className="flex items-center gap-1">
          {[7, 14, 30].map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-sm transition-colors',
                days === d
                  ? 'bg-accent text-white'
                  : 'bg-bg-card border border-border text-text hover:bg-bg-hover'
              )}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* Signal feed */}
      {isLoading ? (
        <div className="text-text/50 py-12 text-center">Loading signals...</div>
      ) : signals && signals.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {signals.map(sig => (
            <SignalCard
              key={sig.id}
              signal={sig}
              competitorName={compMap.get(sig.competitor_id) ?? 'Unknown'}
            />
          ))}
        </div>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center">
          <Zap size={32} className="text-text/30 mx-auto mb-3" />
          <p className="text-text/50">No signals in the last {days} days.</p>
          <p className="text-sm text-text/40 mt-1">Trigger a scrape or wait for the daily run.</p>
        </div>
      )}
    </div>
  )
}
