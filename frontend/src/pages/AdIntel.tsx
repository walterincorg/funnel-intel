import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Megaphone, Play, TrendingUp, Trophy, Sparkles, ArrowRightLeft, X, Zap, ExternalLink, CheckCircle, XCircle, Clock, ChevronDown, ChevronRight, Lightbulb, Target } from 'lucide-react'
import { api, type Ad, type AdSignal, type AdSnapshot, type AdScrapeRun, type AdBriefing, type WinnerAd } from '@/api/client'
import { cn } from '@/lib/utils'

const SIGNAL_CONFIG: Record<string, { label: string; icon: typeof Megaphone; color: string; bg: string }> = {
  new_ad: { label: 'New Ad', icon: Sparkles, color: 'text-info', bg: 'bg-info/10' },
  proven_winner: { label: 'Winner', icon: Trophy, color: 'text-success', bg: 'bg-success/10' },
  count_spike: { label: 'Spike', icon: TrendingUp, color: 'text-danger', bg: 'bg-danger/10' },
  copy_change: { label: 'Copy Change', icon: ArrowRightLeft, color: 'text-warning', bg: 'bg-warning/10' },
  failed_test: { label: 'Failed', icon: X, color: 'text-danger', bg: 'bg-danger/10' },
}

const SEVERITY_COLOR: Record<string, string> = {
  high: 'bg-danger/20 text-danger',
  medium: 'bg-warning/20 text-warning',
  low: 'bg-info/20 text-info',
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function AdDetailModal({ adId, onClose }: { adId: string; onClose: () => void }) {
  const { data: ad, isLoading: adLoading } = useQuery<Ad>({
    queryKey: ['ad', adId],
    queryFn: () => api.getAd(adId),
  })

  const { data: snapshots, isLoading: snapLoading } = useQuery<AdSnapshot[]>({
    queryKey: ['ad-snapshots', adId],
    queryFn: () => api.getAdSnapshots(adId),
  })

  const snap = snapshots?.[0]
  const isLoading = adLoading || snapLoading

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-bg-card rounded-xl border border-border w-full max-w-lg max-h-[85vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-5 border-b border-border">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-text-bright">
              {snap?.headline || ad?.advertiser_name || 'Ad Creative'}
            </span>
            {ad?.status && (
              <span className={cn(
                'px-2 py-0.5 rounded-full text-xs font-medium',
                ad.status === 'ACTIVE' ? 'bg-success/10 text-success' : 'bg-bg text-text/50'
              )}>
                {ad.status}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-bg-hover rounded transition-colors text-text/50 hover:text-text"
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {isLoading ? (
            <div className="text-text/50 text-sm text-center py-8">Loading...</div>
          ) : (
            <>
              {snap?.video_url ? (
                <video
                  src={snap.video_url}
                  controls
                  className="w-full rounded-lg max-h-64 object-contain bg-bg"
                />
              ) : snap?.image_url ? (
                <img
                  src={snap.image_url}
                  alt="Ad creative"
                  className="w-full rounded-lg max-h-64 object-contain bg-bg"
                />
              ) : (
                <div className="w-full h-32 rounded-lg bg-bg flex items-center justify-center text-text/30 text-sm">
                  No media
                </div>
              )}

              {snap?.body_text && (
                <p className="text-sm text-text/80 leading-relaxed">{snap.body_text}</p>
              )}

              {snap?.cta && (
                <span className="inline-block px-3 py-1 rounded-lg bg-accent/10 text-accent text-xs font-medium">
                  {snap.cta}
                </span>
              )}

              <div className="space-y-2 pt-1 border-t border-border/50">
                {snap?.platforms && snap.platforms.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {snap.platforms.map(p => (
                      <span key={p} className="px-2 py-0.5 rounded bg-bg text-xs text-text/60">{p}</span>
                    ))}
                  </div>
                )}

                {(ad?.first_seen_at || snap?.start_date) && (
                  <p className="text-xs text-text/50">
                    {snap?.start_date
                      ? `Running since ${snap.start_date}${snap.stop_date ? ` · ended ${snap.stop_date}` : ''}`
                      : `First seen ${ad!.first_seen_at!.slice(0, 10)}`
                    }
                  </p>
                )}

                {snap?.impression_range != null && (
                  <p className="text-xs text-text/50">Reach: {String(snap.impression_range)}</p>
                )}
              </div>

              {(snap?.landing_page_url || ad?.landing_page_url) && (
                <a
                  href={snap?.landing_page_url || ad?.landing_page_url || '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-xs text-accent hover:underline"
                >
                  <ExternalLink size={12} />
                  View landing page
                </a>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function BriefingSection({ briefing }: { briefing: AdBriefing }) {
  return (
    <div className="bg-bg-card rounded-xl border border-accent/20 p-6 mb-8">
      <div className="flex items-start justify-between mb-4">
        <div>
          <p className="text-xs text-text/40 uppercase tracking-wide mb-1">Weekly Briefing</p>
          <h2 className="text-lg font-semibold text-text-bright">{briefing.headline}</h2>
        </div>
        <span className="text-xs text-text/40 shrink-0 ml-4">{briefing.briefing_date}</span>
      </div>

      <p className="text-sm text-text/80 leading-relaxed mb-4">{briefing.summary}</p>

      <div className="flex items-start gap-2 p-3 rounded-lg bg-accent/5 border border-accent/10">
        <Lightbulb size={16} className="text-accent shrink-0 mt-0.5" />
        <div>
          <p className="text-xs text-accent font-medium uppercase tracking-wide mb-0.5">Suggested Action</p>
          <p className="text-sm text-text-bright">{briefing.suggested_action}</p>
        </div>
      </div>

      {briefing.competitor_moves.length > 0 && (
        <div className="mt-4 pt-4 border-t border-border/50">
          <p className="text-xs text-text/40 uppercase tracking-wide mb-2">Competitor Moves</p>
          <div className="space-y-1.5">
            {briefing.competitor_moves.map((move, i) => (
              <div key={i} className="flex items-start gap-2 text-sm">
                <Target size={14} className="text-text/30 shrink-0 mt-0.5" />
                <span>
                  <span className="font-medium text-text-bright">{move.competitor_name}:</span>{' '}
                  <span className="text-text/70">{move.move_summary}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function WinnerCard({ winner, onClick }: { winner: WinnerAd; onClick: () => void }) {
  return (
    <div
      className="bg-bg-card rounded-xl border border-border overflow-hidden cursor-pointer hover:border-accent/30 transition-colors"
      onClick={onClick}
    >
      {winner.video_url ? (
        <video
          src={winner.video_url}
          className="w-full h-40 object-cover bg-bg"
          muted
        />
      ) : winner.image_url ? (
        <img
          src={winner.image_url}
          alt={winner.headline || 'Ad creative'}
          className="w-full h-40 object-cover bg-bg"
        />
      ) : (
        <div className="w-full h-40 bg-bg flex items-center justify-center text-text/20 text-sm">
          No media
        </div>
      )}

      <div className="p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-text/50">{winner.competitor_name}</span>
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-success/10 text-success">
            <Trophy size={10} />
            {winner.days_active}d
          </span>
        </div>

        {winner.headline && (
          <p className="text-sm font-medium text-text-bright line-clamp-2 mb-1">{winner.headline}</p>
        )}

        {winner.body_text && (
          <p className="text-xs text-text/60 line-clamp-2">{winner.body_text}</p>
        )}

        {winner.cta && (
          <span className="inline-block mt-2 px-2 py-0.5 rounded bg-accent/10 text-accent text-xs">
            {winner.cta}
          </span>
        )}
      </div>
    </div>
  )
}

function SignalCard({
  signal,
  competitorName,
  onClick,
}: {
  signal: AdSignal
  competitorName: string
  onClick?: () => void
}) {
  const config = SIGNAL_CONFIG[signal.signal_type] ?? SIGNAL_CONFIG.new_ad
  const Icon = config.icon

  return (
    <div
      className={cn(
        'bg-bg-card rounded-xl border border-border p-4 transition-colors',
        onClick ? 'cursor-pointer hover:border-accent/30' : 'hover:border-accent/30'
      )}
      onClick={onClick}
    >
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

function ScrapeRunStrip({ runs }: { runs: AdScrapeRun[] }) {
  const meaningful = runs.filter(r => r.status === 'completed' || r.status === 'failed' || r.status === 'pending' || r.status === 'running')
  if (!meaningful.length) return null

  return (
    <div className="mt-8 flex flex-wrap items-center gap-2">
      <span className="text-xs text-text/50">Last scrapes:</span>
      {meaningful.slice(0, 5).map(run => {
        const isOk = run.status === 'completed'
        const isFail = run.status === 'failed'
        const StatusIcon = isOk ? CheckCircle : isFail ? XCircle : Clock
        const color = isOk ? 'text-success' : isFail ? 'text-danger' : 'text-warning'
        const ts = run.completed_at || run.started_at || run.created_at
        return (
          <span
            key={run.id}
            className={cn('inline-flex items-center gap-1 text-xs', color)}
            title={run.error ?? undefined}
          >
            <StatusIcon size={12} />
            {ts ? timeAgo(ts) : run.status}
            {run.ads_found > 0 && <span className="text-text/40">({run.ads_found} ads)</span>}
          </span>
        )
      })}
    </div>
  )
}

export function AdIntel() {
  const queryClient = useQueryClient()
  const [selectedAdId, setSelectedAdId] = useState<string | null>(null)
  const [showSignals, setShowSignals] = useState(false)
  const [filterCompetitor, setFilterCompetitor] = useState<string>('')
  const [filterType, setFilterType] = useState<string>('')
  const [days, setDays] = useState(7)

  const { data: competitors } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  const { data: briefing } = useQuery({
    queryKey: ['ad-briefing'],
    queryFn: api.getBriefing,
  })

  const { data: winnersAllTime, isLoading: winnersAllTimeLoading } = useQuery({
    queryKey: ['ad-winners-alltime'],
    queryFn: () => api.listWinners(6, 'all-time'),
  })

  const { data: winnersRecent, isLoading: winnersRecentLoading } = useQuery({
    queryKey: ['ad-winners-recent'],
    queryFn: () => api.listWinners(6, 'recent'),
  })

  const { data: signals } = useQuery({
    queryKey: ['ad-signals', filterCompetitor, filterType, days],
    queryFn: () => api.listAdSignals({
      competitor_id: filterCompetitor || undefined,
      signal_type: filterType || undefined,
      days,
    }),
    enabled: showSignals,
  })

  const { data: summary } = useQuery({
    queryKey: ['ad-signals-summary', days],
    queryFn: () => api.adSignalsSummary(days),
  })

  const { data: scrapeRuns } = useQuery({
    queryKey: ['ad-scrape-runs'],
    queryFn: api.listAdScrapeRuns,
    refetchInterval: 5000,
  })

  const scrapeMutation = useMutation({
    mutationFn: api.triggerAdScrape,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ad-scrape-runs'] })
    },
  })

  const scrapeActive = (scrapeRuns ?? []).some(r => r.status === 'pending' || r.status === 'running')

  const compMap = new Map<string, string>()
  for (const c of competitors ?? []) {
    compMap.set(c.id, c.name)
  }

  const summaryMap = new Map<string, number>()
  for (const s of summary ?? []) {
    summaryMap.set(s.signal_type, s.count)
  }
  const provenWinners = summaryMap.get('proven_winner') ?? 0
  const countSpikes = summaryMap.get('count_spike') ?? 0
  const totalSignals = (summary ?? []).reduce((acc, s) => acc + s.count, 0)

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-text-bright flex items-center gap-2">
            <Megaphone size={24} className="text-accent" />
            Ad Intelligence
          </h1>
          <p className="text-sm text-text/60 mt-1">What competitors are doing — and what you should do about it.</p>
        </div>
        <div className="flex items-center gap-2">
          {scrapeActive && (
            <span className="text-sm text-text/50 animate-pulse">Scraping...</span>
          )}
          <button
            onClick={() => scrapeMutation.mutate()}
            disabled={scrapeMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
          >
            <Play size={16} />
            {scrapeMutation.isPending ? 'Queuing...' : scrapeActive ? 'Restart Scrape' : 'Scrape Now'}
          </button>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <p className="text-xs text-text/60 uppercase tracking-wide">Winners ({days}d)</p>
          <p className="text-2xl font-semibold text-success mt-1">{provenWinners}</p>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <p className="text-xs text-text/60 uppercase tracking-wide">Spikes ({days}d)</p>
          <p className="text-2xl font-semibold text-danger mt-1">{countSpikes}</p>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <p className="text-xs text-text/60 uppercase tracking-wide">Total Signals ({days}d)</p>
          <p className="text-2xl font-semibold text-text-bright mt-1">{totalSignals}</p>
        </div>
      </div>

      {/* CEO Briefing */}
      {briefing && <BriefingSection briefing={briefing} />}

      {/* Recent Winners (30d) */}
      {winnersRecent && winnersRecent.length > 0 && (
        <div className="mb-8">
          <h2 className="text-sm font-medium text-text-bright flex items-center gap-2 mb-4">
            <Zap size={16} className="text-info" />
            Recent Winners
            <span className="text-xs text-text/40 font-normal">running 30+ days</span>
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {winnersRecent.map(w => (
              <WinnerCard
                key={w.ad_id}
                winner={w}
                onClick={() => setSelectedAdId(w.ad_id)}
              />
            ))}
          </div>
        </div>
      )}

      {winnersRecentLoading && !winnersRecent && (
        <div className="text-text/50 py-4 text-center text-sm">Loading recent winners...</div>
      )}

      {/* All-Time Winners */}
      {winnersAllTime && winnersAllTime.length > 0 && (
        <div className="mb-8">
          <h2 className="text-sm font-medium text-text-bright flex items-center gap-2 mb-4">
            <Trophy size={16} className="text-success" />
            All-Time Winners
            <span className="text-xs text-text/40 font-normal">running 14+ days</span>
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {winnersAllTime.map(w => (
              <WinnerCard
                key={w.ad_id}
                winner={w}
                onClick={() => setSelectedAdId(w.ad_id)}
              />
            ))}
          </div>
        </div>
      )}

      {winnersAllTimeLoading && !winnersAllTime && (
        <div className="text-text/50 py-4 text-center text-sm">Loading all-time winners...</div>
      )}

      {/* Detailed Signals (collapsed by default) */}
      <div className="border-t border-border pt-6">
        <button
          onClick={() => setShowSignals(!showSignals)}
          className="flex items-center gap-2 text-sm font-medium text-text/60 hover:text-text-bright transition-colors"
        >
          {showSignals ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          Detailed Signals ({totalSignals})
        </button>

        {showSignals && (
          <div className="mt-4">
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
            {signals && signals.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {signals.map(sig => (
                  <SignalCard
                    key={sig.id}
                    signal={sig}
                    competitorName={compMap.get(sig.competitor_id) ?? 'Unknown'}
                    onClick={sig.ad_id ? () => setSelectedAdId(sig.ad_id) : undefined}
                  />
                ))}
              </div>
            ) : (
              <div className="bg-bg-card rounded-xl border border-border p-8 text-center">
                <Zap size={32} className="text-text/30 mx-auto mb-3" />
                <p className="text-text/50">No signals in the last {days} days.</p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Scrape run history */}
      <ScrapeRunStrip runs={scrapeRuns ?? []} />

      {/* Ad creative modal */}
      {selectedAdId && (
        <AdDetailModal adId={selectedAdId} onClose={() => setSelectedAdId(null)} />
      )}
    </div>
  )
}
