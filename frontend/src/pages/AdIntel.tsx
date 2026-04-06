import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Megaphone, Play, TrendingUp, Trophy, Sparkles, ArrowRightLeft, X, Zap, ExternalLink, CheckCircle, XCircle, Clock, ChevronDown, ChevronRight, Brain } from 'lucide-react'
import { api, type Ad, type AdSignal, type AdSnapshot, type AdScrapeRun, type CompetitorAnalysis } from '@/api/client'
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
        {/* Header */}
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
              {/* Media */}
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

              {/* Copy */}
              {snap?.body_text && (
                <p className="text-sm text-text/80 leading-relaxed">{snap.body_text}</p>
              )}

              {snap?.cta && (
                <span className="inline-block px-3 py-1 rounded-lg bg-accent/10 text-accent text-xs font-medium">
                  {snap.cta}
                </span>
              )}

              {/* Meta */}
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

              {/* Landing page */}
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
  const [filterCompetitor, setFilterCompetitor] = useState<string>('')
  const [filterType, setFilterType] = useState<string>('')
  const [days, setDays] = useState(7)
  const [selectedAdId, setSelectedAdId] = useState<string | null>(null)
  const [analysisOpen, setAnalysisOpen] = useState(true)

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

  const { data: scrapeRuns } = useQuery({
    queryKey: ['ad-scrape-runs'],
    queryFn: api.listAdScrapeRuns,
    refetchInterval: 5000,
  })

  const { data: analyses } = useQuery({
    queryKey: ['ad-analyses', filterCompetitor],
    queryFn: () => api.listAnalyses(filterCompetitor || undefined),
  })

  const scrapeMutation = useMutation({
    mutationFn: api.triggerAdScrape,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ad-scrape-runs'] })
    },
  })

  // Derive scrape button state from DB, not from mutation lifecycle
  const scrapeActive = (scrapeRuns ?? []).some(r => r.status === 'pending' || r.status === 'running')

  const compMap = new Map<string, string>()
  for (const c of competitors ?? []) {
    compMap.set(c.id, c.name)
  }

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
        {scrapeActive ? (
          <span className="text-sm text-text/50 px-4 py-2">Scraping…</span>
        ) : (
          <button
            onClick={() => scrapeMutation.mutate()}
            disabled={scrapeMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
          >
            <Play size={16} />
            {scrapeMutation.isPending ? 'Queuing...' : 'Scrape Now'}
          </button>
        )}
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

      {/* Analysis section */}
      {analyses && analyses.length > 0 && (
        <div className="mb-8">
          <button
            onClick={() => setAnalysisOpen(!analysisOpen)}
            className="flex items-center gap-2 text-sm font-medium text-text-bright mb-4 hover:text-accent transition-colors"
          >
            {analysisOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            <Brain size={16} className="text-accent" />
            Strategy Analysis ({analyses.length} competitor{analyses.length !== 1 ? 's' : ''})
          </button>

          {analysisOpen && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {analyses.map(a => (
                <div key={a.id} className="bg-bg-card rounded-xl border border-border p-5">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-text-bright">
                      {compMap.get(a.competitor_id) ?? 'Unknown'}
                    </h3>
                    <span className="text-xs text-text/40">{a.analysis_date}</span>
                  </div>
                  <p className="text-sm text-text/80 leading-relaxed mb-3">{a.summary}</p>
                  {a.strategy_tags.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mb-3">
                      {a.strategy_tags.map(tag => (
                        <span key={tag} className="px-2 py-0.5 rounded-full bg-accent/10 text-accent text-xs">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                  {a.top_ads.length > 0 && (
                    <div className="space-y-1.5 pt-2 border-t border-border/50">
                      <p className="text-xs text-text/50 uppercase tracking-wide">Top Ads</p>
                      {a.top_ads.slice(0, 3).map((ad, i) => (
                        <div
                          key={ad.meta_ad_id}
                          className={cn(
                            'text-xs text-text/70 flex items-start gap-1.5',
                            ad.ad_id ? 'cursor-pointer hover:text-accent' : ''
                          )}
                          onClick={ad.ad_id ? () => setSelectedAdId(ad.ad_id) : undefined}
                        >
                          <span className="text-accent/60 font-mono">{i + 1}.</span>
                          <span>{ad.reason}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Signal feed */}
      {isLoading ? (
        <div className="text-text/50 py-12 text-center">Loading signals...</div>
      ) : signals && signals.length > 0 ? (
        <>
          {totalSignals > signals.length && (
            <p className="text-xs text-text/40 mb-3">
              Showing {signals.length} of {totalSignals} signals — filter by competitor or type to narrow down
            </p>
          )}
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
        </>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center">
          <Zap size={32} className="text-text/30 mx-auto mb-3" />
          <p className="text-text/50">No signals in the last {days} days.</p>
          <p className="text-sm text-text/40 mt-1">Trigger a scrape or wait for the daily run.</p>
        </div>
      )}

      {/* Scrape run history */}
      <ScrapeRunStrip runs={scrapeRuns ?? []} />

      {/* Ad creative modal */}
      {selectedAdId && (
        <AdDetailModal adId={selectedAdId} onClose={() => setSelectedAdId(null)} />
      )}
    </div>
  )
}
