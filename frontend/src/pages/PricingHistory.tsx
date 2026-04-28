import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type PricingSnapshot, type Competitor, type VisionPlan } from '@/api/client'
import { formatDate } from '@/lib/utils'
import { TrendingUp, TrendingDown, Plus, Minus, Clock, Image as ImageIcon, X, AlertTriangle, Tag } from 'lucide-react'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parsePrice(raw: string | number | null | undefined): number | null {
  if (raw === null || raw === undefined) return null
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null
  const n = parseFloat(String(raw).replace(/[^0-9.]/g, ''))
  return Number.isNaN(n) ? null : n
}

function pickCurrency(snapshot: PricingSnapshot): string {
  return (
    snapshot.metadata?.vision?.currency
    ?? snapshot.plans?.find(p => p.currency)?.currency
    ?? 'USD'
  )
}

// Stable plan identity: prefer the v2 vision plan_id, otherwise normalise the
// display name into a slug so renames don't fragment the chart.
function planSlug(name: string | null | undefined): string {
  if (!name) return 'unknown'
  return name
    .toLowerCase()
    .replace(/\(.*?\)/g, ' ')
    .replace(/(intro|introductory|first|trial|plan|supply)/g, ' ')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32) || 'plan'
}

// ---------------------------------------------------------------------------
// Series builder
// ---------------------------------------------------------------------------

interface NormalizedPoint {
  snapshotIndex: number
  date: string
  totalPrice: number | null
  monthlyEquivalent: number | null
  perDayPrice: number | null
  cycleWeeks: number | null
  badges: string[]
  rawDisplay: string
}

interface PlanSeries {
  planKey: string           // stable identifier
  displayName: string       // most recent name
  cycleWeeks: number | null
  intro: (NormalizedPoint | null)[]
  renewal: (NormalizedPoint | null)[]
  isMostPopular: boolean
}

interface NormalizedHistory {
  snapshots: PricingSnapshot[]
  series: PlanSeries[]
  currency: string
  hasVision: boolean
}

function normalizeHistory(snapshots: PricingSnapshot[]): NormalizedHistory {
  const sorted = [...snapshots].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  )
  const seriesByKey = new Map<string, PlanSeries>()
  const ensureSeries = (key: string, name: string, cycle: number | null): PlanSeries => {
    let s = seriesByKey.get(key)
    if (!s) {
      s = {
        planKey: key,
        displayName: name,
        cycleWeeks: cycle,
        intro: new Array(sorted.length).fill(null),
        renewal: new Array(sorted.length).fill(null),
        isMostPopular: false,
      }
      seriesByKey.set(key, s)
    } else {
      s.displayName = name      // always show the most-recent label
      if (cycle && !s.cycleWeeks) s.cycleWeeks = cycle
    }
    return s
  }

  let hasVision = false
  let currency = 'USD'

  sorted.forEach((snapshot, idx) => {
    if (idx === 0) currency = pickCurrency(snapshot)
    else if (snapshot.metadata?.vision?.currency) currency = snapshot.metadata.vision.currency

    const visionPlans: VisionPlan[] | null = snapshot.metadata?.vision?.plans ?? null
    if (visionPlans && visionPlans.length > 0) {
      hasVision = true
      for (const plan of visionPlans) {
        const key = (plan.plan_id || planSlug(plan.display_name)).toLowerCase()
        const series = ensureSeries(key, plan.display_name || plan.plan_id || 'Plan', plan.billing_cycle_weeks)
        if (plan.is_most_popular) series.isMostPopular = true
        if (plan.intro && plan.intro.total_price !== null && plan.intro.total_price !== undefined) {
          series.intro[idx] = {
            snapshotIndex: idx,
            date: snapshot.created_at,
            totalPrice: plan.intro.total_price,
            monthlyEquivalent: plan.monthly_equivalent ?? null,
            perDayPrice: plan.intro.per_day_price ?? null,
            cycleWeeks: plan.billing_cycle_weeks,
            badges: plan.badges ?? [],
            rawDisplay: `${plan.intro.total_price} ${currency}` + (plan.intro.label ? ` · ${plan.intro.label}` : ''),
          }
        }
        if (plan.renewal && plan.renewal.total_price !== null && plan.renewal.total_price !== undefined) {
          series.renewal[idx] = {
            snapshotIndex: idx,
            date: snapshot.created_at,
            totalPrice: plan.renewal.total_price,
            monthlyEquivalent: plan.renewal_monthly_equivalent ?? null,
            perDayPrice: plan.renewal.per_day_price ?? null,
            cycleWeeks: plan.billing_cycle_weeks,
            badges: plan.badges ?? [],
            rawDisplay: `${plan.renewal.total_price} ${currency}` + (plan.renewal.billed_every ? ` / ${plan.renewal.billed_every}` : ''),
          }
        }
      }
      return
    }

    // Legacy snapshot — derive what we can from `plans`.
    for (const plan of snapshot.plans ?? []) {
      const key = (plan.plan_id || planSlug(plan.name)).toLowerCase()
      const series = ensureSeries(key, plan.name, null)
      const total = parsePrice(plan.price)
      if (total === null) continue
      const point: NormalizedPoint = {
        snapshotIndex: idx,
        date: snapshot.created_at,
        totalPrice: total,
        monthlyEquivalent: plan.monthly_equivalent ?? null,
        perDayPrice: null,
        cycleWeeks: null,
        badges: plan.features ?? [],
        rawDisplay: `${plan.price} ${plan.currency}` + (plan.period ? ` · ${plan.period}` : ''),
      }
      if (plan.price_kind === 'renewal') {
        series.renewal[idx] = point
      } else {
        series.intro[idx] = point
      }
    }
  })

  return {
    snapshots: sorted,
    series: [...seriesByKey.values()].filter(s => s.intro.some(Boolean) || s.renewal.some(Boolean)),
    currency,
    hasVision,
  }
}

// ---------------------------------------------------------------------------
// Change detection (operates on intro prices — that's what users actually pay)
// ---------------------------------------------------------------------------

type ChangeKind = 'increased' | 'decreased' | 'added' | 'removed'

interface PriceChange {
  snapshotIndex: number
  date: string
  planName: string
  kind: ChangeKind
  oldPrice: number | null
  newPrice: number | null
  delta: number | null
  suspicious: boolean
}

function detectChanges(history: NormalizedHistory): PriceChange[] {
  const changes: PriceChange[] = []
  for (const series of history.series) {
    let prev: NormalizedPoint | null = null
    series.intro.forEach((point, i) => {
      if (i === 0 && point) { prev = point; return }
      if (!point) return
      if (!prev) {
        prev = point
        return
      }
      if (Math.abs(point.totalPrice! - prev.totalPrice!) >= 0.01) {
        const kind: ChangeKind = point.totalPrice! > prev.totalPrice! ? 'increased' : 'decreased'
        const delta = point.totalPrice! - prev.totalPrice!
        // Suspicious = a >50% jump that's almost certainly intro vs renewal mixup
        // rather than a real price change. We flag it but still show.
        const ratio = Math.abs(delta) / Math.max(prev.totalPrice!, 1)
        const suspicious = ratio > 0.5 && Math.abs(delta) > 5
        changes.push({
          snapshotIndex: i,
          date: point.date,
          planName: series.displayName,
          kind,
          oldPrice: prev.totalPrice,
          newPrice: point.totalPrice,
          delta,
          suspicious,
        })
      }
      prev = point
    })
  }
  return changes
}

// ---------------------------------------------------------------------------
// Chart
// ---------------------------------------------------------------------------

const PLAN_COLORS = ['#818cf8', '#34d399', '#fbbf24', '#60a5fa', '#f87171', '#c084fc', '#22d3ee']
const CHART_W = 640
const CHART_AREA_H = 140
const PAD = { top: 8, right: 16, bottom: 26, left: 48 }
const LEGEND_ROW_H = 16

type ChartMetric = 'monthly' | 'tile'

function PriceChart({ history, metric, suspiciousIndices }: {
  history: NormalizedHistory
  metric: ChartMetric
  suspiciousIndices: Set<number>
}) {
  const { snapshots, series, currency } = history
  if (snapshots.length < 2 || series.length === 0) return null

  const valueOf = (p: NormalizedPoint | null) => {
    if (!p) return null
    return metric === 'monthly' ? (p.monthlyEquivalent ?? p.totalPrice) : p.totalPrice
  }

  const all: number[] = []
  for (const s of series) {
    for (const p of s.intro) { const v = valueOf(p); if (v !== null) all.push(v) }
    for (const p of s.renewal) { const v = valueOf(p); if (v !== null) all.push(v) }
  }
  if (all.length === 0) return null
  const minVal = Math.min(...all)
  const maxVal = Math.max(...all)
  const valRange = (maxVal - minVal) || Math.max(1, maxVal * 0.1)

  const innerW = CHART_W - PAD.left - PAD.right
  const innerH = CHART_AREA_H - PAD.top - PAD.bottom
  const xPos = (i: number) => PAD.left + (snapshots.length === 1 ? innerW / 2 : (i / (snapshots.length - 1)) * innerW)
  const yPos = (v: number) => PAD.top + innerH - ((v - minVal) / valRange) * innerH

  const labelIndices = new Set<number>([0, snapshots.length - 1])
  const step = Math.max(1, Math.floor(snapshots.length / 4))
  for (let i = step; i < snapshots.length - 1; i += step) labelIndices.add(i)
  const sortedLabels = [...labelIndices].sort((a, b) => a - b)

  const legendCols = 2
  const legendRows = Math.ceil(series.length / legendCols)
  const totalH = CHART_AREA_H + Math.max(legendRows * LEGEND_ROW_H + 8, 8)
  const colW = (CHART_W - PAD.left) / legendCols

  return (
    <svg viewBox={`0 0 ${CHART_W} ${totalH}`} width="100%" style={{ maxWidth: `${CHART_W}px` }} className="block">
      {[0, 0.25, 0.5, 0.75, 1].map(t => {
        const y = PAD.top + innerH * (1 - t)
        const label = (currency === 'USD' ? '$' : currency === 'EUR' ? '€' : '') + (minVal + valRange * t).toFixed(0)
        return (
          <g key={t}>
            <line x1={PAD.left} y1={y} x2={CHART_W - PAD.right} y2={y} stroke="#2e303a" strokeWidth="1" />
            <text x={PAD.left - 6} y={y + 4} textAnchor="end" fontSize="10" fill="#6b7280">{label}</text>
          </g>
        )
      })}

      {sortedLabels.map(i => (
        <text key={i} x={xPos(i)} y={CHART_AREA_H - 4} textAnchor="middle" fontSize="10" fill="#6b7280">
          {new Date(snapshots[i].created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
        </text>
      ))}

      {[...suspiciousIndices].map(i => (
        <line
          key={`sus-${i}`}
          x1={xPos(i)} y1={PAD.top}
          x2={xPos(i)} y2={PAD.top + innerH}
          stroke="#f87171" strokeWidth="1" strokeDasharray="2,3" opacity="0.5"
        >
          <title>Suspicious price jump (likely intro vs renewal mixup)</title>
        </line>
      ))}

      {series.map((s, si) => {
        const color = PLAN_COLORS[si % PLAN_COLORS.length]
        const introPoints: { x: number; y: number; i: number; v: number; p: NormalizedPoint }[] = []
        s.intro.forEach((p, i) => {
          const v = valueOf(p)
          if (v !== null && p) introPoints.push({ x: xPos(i), y: yPos(v), i, v, p })
        })
        const renewalPoints: { x: number; y: number; i: number; v: number; p: NormalizedPoint }[] = []
        s.renewal.forEach((p, i) => {
          const v = valueOf(p)
          if (v !== null && p) renewalPoints.push({ x: xPos(i), y: yPos(v), i, v, p })
        })
        const introPath = introPoints.map((p, pi) => `${pi === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
        const renewalPath = renewalPoints.map((p, pi) => `${pi === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
        return (
          <g key={s.planKey}>
            {renewalPath && (
              <path d={renewalPath} fill="none" stroke={color} strokeWidth="1.4" strokeDasharray="4,3" opacity="0.55" />
            )}
            {renewalPoints.map(p => (
              <circle key={`r${p.i}`} cx={p.x} cy={p.y} r={2} fill="transparent" stroke={color} strokeWidth="1" opacity="0.6">
                <title>{s.displayName} renewal · {p.p.totalPrice} {currency}{p.p.cycleWeeks ? ` / ${p.p.cycleWeeks}w` : ''} · {new Date(p.p.date).toLocaleDateString()}</title>
              </circle>
            ))}
            {introPath && (
              <path d={introPath} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
            )}
            {introPoints.map(p => (
              <circle key={`i${p.i}`} cx={p.x} cy={p.y} r={suspiciousIndices.has(p.i) ? 4 : 2.6}
                      fill={suspiciousIndices.has(p.i) ? color : '#1a1b23'} stroke={color} strokeWidth="1.4">
                <title>{s.displayName} · {p.p.rawDisplay}{p.p.monthlyEquivalent !== null ? ` · ${p.p.monthlyEquivalent.toFixed(2)} ${currency}/mo equiv.` : ''} · {new Date(p.p.date).toLocaleDateString()}</title>
              </circle>
            ))}
          </g>
        )
      })}

      {series.map((s, si) => {
        const col = si % legendCols
        const row = Math.floor(si / legendCols)
        const truncated = s.displayName.length > 24 ? s.displayName.slice(0, 23) + '…' : s.displayName
        return (
          <g key={s.planKey} transform={`translate(${PAD.left + col * colW}, ${CHART_AREA_H + 4 + row * LEGEND_ROW_H})`}>
            <line x1="0" y1="6" x2="14" y2="6" stroke={PLAN_COLORS[si % PLAN_COLORS.length]} strokeWidth="2" />
            <text x="20" y="10" fontSize="10" fill="#9ca3af">
              {truncated}{s.cycleWeeks ? ` · ${s.cycleWeeks}w` : ''}{s.isMostPopular ? ' ★' : ''}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Screenshot lightbox
// ---------------------------------------------------------------------------

function ScreenshotButton({ snapshotId, label = 'View' }: { snapshotId: string; label?: string }) {
  const [open, setOpen] = useState(false)
  const { data } = useQuery({
    queryKey: ['pricing-screenshot', snapshotId],
    queryFn: () => api.pricingScreenshotUrl(snapshotId),
    enabled: open,
    staleTime: 30 * 60 * 1000,
  })

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 text-xs text-text/60 hover:text-accent transition-colors"
        title="View captured pricing screenshot"
      >
        <ImageIcon size={11} /> {label}
      </button>
      {open && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6" onClick={() => setOpen(false)}>
          <button onClick={() => setOpen(false)} className="absolute top-4 right-4 text-white/80 hover:text-white">
            <X size={24} />
          </button>
          <div className="max-h-full max-w-4xl overflow-auto bg-bg-card rounded-xl border border-border" onClick={e => e.stopPropagation()}>
            {data?.url ? (
              <img src={data.url} alt="Pricing screenshot" className="block w-full h-auto" />
            ) : data ? (
              <p className="p-8 text-text/50">Screenshot not available.</p>
            ) : (
              <p className="p-8 text-text/50">Loading…</p>
            )}
          </div>
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Per-plan card (latest snapshot summary)
// ---------------------------------------------------------------------------

function PlanCardLatest({ history }: { history: NormalizedHistory }) {
  if (history.snapshots.length === 0) return null
  const lastIdx = history.snapshots.length - 1
  const snapshot = history.snapshots[lastIdx]
  const { currency } = history

  return (
    <div className="border-b border-border/30">
      <div className="px-5 py-2 flex items-center justify-between">
        <span className="text-xs text-text/40 uppercase tracking-wide font-medium">
          Current pricing — captured {formatDate(snapshot.created_at)}
        </span>
        <ScreenshotButton snapshotId={snapshot.id} label="Screenshot" />
      </div>
      <div className="px-5 pb-3 grid grid-cols-1 md:grid-cols-2 gap-2">
        {history.series.map(s => {
          const intro = s.intro[lastIdx]
          const renewal = s.renewal[lastIdx]
          return (
            <div key={s.planKey} className="flex items-baseline justify-between rounded-lg border border-border/40 px-3 py-2 bg-bg-hover/40">
              <div className="min-w-0">
                <p className="text-sm text-text-bright font-medium truncate">{s.displayName}{s.isMostPopular && <span className="ml-1 text-warning">★</span>}</p>
                {s.cycleWeeks && <p className="text-[11px] text-text/40">{s.cycleWeeks}-week cycle</p>}
              </div>
              <div className="text-right">
                {intro ? (
                  <p className="text-sm text-accent font-semibold">
                    {intro.totalPrice?.toFixed(2)} <span className="text-text/40 font-normal">{currency}</span>
                  </p>
                ) : <p className="text-xs text-text/30">—</p>}
                {renewal && (
                  <p className="text-[11px] text-text/50">renews {renewal.totalPrice?.toFixed(2)} {currency}</p>
                )}
                {intro?.monthlyEquivalent !== null && intro?.monthlyEquivalent !== undefined && (
                  <p className="text-[11px] text-text/40">≈ {intro.monthlyEquivalent.toFixed(2)} {currency}/mo</p>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Per-competitor card
// ---------------------------------------------------------------------------

function ChangeBadge({ kind, suspicious }: { kind: ChangeKind; suspicious: boolean }) {
  const cls = (color: string, bg: string) => `inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${color} ${bg}`
  if (suspicious) {
    return <span className={cls('text-warning', 'bg-warning/10 border border-warning/20')}><AlertTriangle size={10} /> Likely extractor mix-up</span>
  }
  if (kind === 'increased') return <span className={cls('text-danger', 'bg-danger/10 border border-danger/20')}><TrendingUp size={10} /> Higher</span>
  if (kind === 'decreased') return <span className={cls('text-success', 'bg-success/10 border border-success/20')}><TrendingDown size={10} /> Lower</span>
  if (kind === 'added') return <span className={cls('text-info', 'bg-info/10 border border-info/20')}><Plus size={10} /> New plan</span>
  return <span className={cls('text-text/50', 'bg-bg-hover border border-border')}><Minus size={10} /> Removed</span>
}

function CompetitorHistoryCard({ competitor, snapshots }: { competitor: Competitor; snapshots: PricingSnapshot[] }) {
  const history = useMemo(() => normalizeHistory(snapshots), [snapshots])
  const changes = useMemo(() => detectChanges(history), [history])
  const [metric, setMetric] = useState<ChartMetric>('monthly')
  const [showAll, setShowAll] = useState(false)

  const suspiciousIndices = useMemo(
    () => new Set(changes.filter(c => c.suspicious).map(c => c.snapshotIndex)),
    [changes]
  )
  const visibleChanges = showAll ? changes : changes.filter(c => !c.suspicious)
  const lastChange = changes.length > 0 ? changes[changes.length - 1] : null
  const lastChangeDate = lastChange
    ? new Date(lastChange.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : null
  const sorted = history.snapshots
  const versionTag = history.hasVision ? 'v2' : 'v1'

  return (
    <div className="bg-bg-card rounded-xl border border-border overflow-hidden">
      <div className="px-5 py-4 border-b border-border/50 flex items-center justify-between flex-wrap gap-2">
        <div>
          <h3 className="text-text-bright font-medium flex items-center gap-2">
            {competitor.name}
            <span className={`text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 border ${history.hasVision ? 'border-success/30 text-success bg-success/5' : 'border-border text-text/40 bg-bg-hover'}`}>
              extractor {versionTag}
            </span>
          </h3>
          <p className="text-xs text-text/50 mt-0.5">{sorted.length} scan{sorted.length !== 1 ? 's' : ''} · {history.series.length} plan tiles tracked</p>
        </div>
        <div className="flex items-center gap-2">
          {changes.length > 0 && lastChangeDate !== null ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-warning bg-warning/8 border border-warning/20 px-2.5 py-1 rounded-full">
              <Clock size={11} />
              Last change {lastChangeDate}
            </span>
          ) : sorted.length > 1 ? (
            <span className="text-xs text-success bg-success/8 border border-success/20 px-2.5 py-1 rounded-full">No price changes</span>
          ) : null}
        </div>
      </div>

      {sorted.length === 0 ? (
        <p className="px-5 py-6 text-sm text-text/40 italic text-center">No pricing data captured yet.</p>
      ) : (
        <>
          <PlanCardLatest history={history} />

          {sorted.length >= 2 && (
            <>
              <div className="px-5 pt-3 flex items-center justify-between flex-wrap gap-2">
                <span className="text-xs text-text/40 uppercase tracking-wide font-medium">Price history</span>
                <div className="inline-flex items-center gap-0.5 rounded-md border border-border overflow-hidden">
                  <button
                    onClick={() => setMetric('monthly')}
                    className={`text-[11px] px-2 py-1 ${metric === 'monthly' ? 'bg-accent/15 text-accent' : 'text-text/50 hover:text-text/70'}`}
                    title="Normalize all plans to monthly equivalent — apples to apples"
                  >Monthly $/mo</button>
                  <button
                    onClick={() => setMetric('tile')}
                    className={`text-[11px] px-2 py-1 ${metric === 'tile' ? 'bg-accent/15 text-accent' : 'text-text/50 hover:text-text/70'}`}
                    title="Show the tile total price (intro)"
                  >Tile total</button>
                </div>
              </div>
              <div className="px-5 pt-1 pb-2">
                <PriceChart history={history} metric={metric} suspiciousIndices={suspiciousIndices} />
              </div>
              <div className="px-5 pb-3 text-[11px] text-text/40 leading-snug">
                Solid line = price you would pay today (intro). Dashed = renewal/full price.
                Red dotted vertical lines mark suspicious jumps (likely intro-vs-renewal mix-up by the extractor).
              </div>
            </>
          )}

          {changes.length > 0 ? (
            <div className="border-t border-border/30">
              <div className="px-5 py-2 flex items-center justify-between">
                <span className="text-xs text-text/40 uppercase tracking-wide font-medium">Change log</span>
                {suspiciousIndices.size > 0 && (
                  <button
                    onClick={() => setShowAll(v => !v)}
                    className="text-xs text-text/40 hover:text-text/70 transition-colors"
                  >
                    {showAll ? `Hide ${changes.length - visibleChanges.length} suspicious` : `Show ${changes.length - visibleChanges.length} suspicious`}
                  </button>
                )}
              </div>
              {visibleChanges.length === 0 ? (
                <p className="px-5 py-3 text-sm text-text/40 border-t border-border/20">No genuine price changes detected.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-text/40 uppercase tracking-wide border-b border-border/20">
                      <th className="text-left px-5 py-2 font-medium">Date</th>
                      <th className="text-left px-5 py-2 font-medium">Plan</th>
                      <th className="text-left px-5 py-2 font-medium">Change</th>
                      <th className="text-right px-5 py-2 font-medium">Price</th>
                      <th className="text-right px-5 py-2 font-medium">Proof</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...visibleChanges].reverse().map((c, i) => {
                      const snapshot = sorted[c.snapshotIndex]
                      return (
                        <tr key={i} className="border-b border-border/15 last:border-0 hover:bg-bg-hover/40 transition-colors">
                          <td className="px-5 py-2.5 text-text/50 text-xs whitespace-nowrap">{formatDate(c.date)}</td>
                          <td className="px-5 py-2.5 text-text-bright font-medium">{c.planName}</td>
                          <td className="px-5 py-2.5"><ChangeBadge kind={c.kind} suspicious={c.suspicious} /></td>
                          <td className="px-5 py-2.5 text-right text-xs text-text/60">
                            <span className="line-through text-text/30">{c.oldPrice?.toFixed(2)}</span>
                            {' → '}
                            <span className={c.kind === 'increased' ? 'text-danger' : 'text-success'}>
                              {c.newPrice?.toFixed(2)}
                            </span>
                            {' '}<span className="text-text/30">{history.currency}</span>
                          </td>
                          <td className="px-5 py-2.5 text-right">
                            {snapshot && <ScreenshotButton snapshotId={snapshot.id} label="Open" />}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )}
            </div>
          ) : sorted.length > 1 ? (
            <p className="px-5 py-3 text-sm text-text/40 border-t border-border/20">No price changes across {sorted.length} scans.</p>
          ) : null}

          {/* Extractor hints — surface what the model said in the latest snapshot */}
          {history.snapshots[sorted.length - 1]?.metadata?.vision?.notes && (
            <div className="px-5 py-3 border-t border-border/30 text-[11px] text-text/45 leading-snug flex gap-2">
              <Tag size={11} className="mt-[2px] flex-shrink-0" />
              <span><span className="text-text/60 font-medium">Extractor note: </span>{history.snapshots[sorted.length - 1]!.metadata!.vision!.notes}</span>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function PricingHistory() {
  const { data: snapshots, isLoading } = useQuery({
    queryKey: ['pricing-all'],
    queryFn: () => api.listPricingAll(),
  })

  const { data: competitors } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  if (isLoading) {
    return <div className="text-text/50 py-12 text-center">Loading...</div>
  }

  const compMap = new Map((competitors ?? []).map(c => [c.id, c]))
  const byCompetitor = new Map<string, PricingSnapshot[]>()
  for (const s of snapshots ?? []) {
    const list = byCompetitor.get(s.competitor_id) ?? []
    list.push(s)
    byCompetitor.set(s.competitor_id, list)
  }

  const competitorEntries = Array.from(byCompetitor.entries())
    .map(([id, snaps]) => ({ competitor: compMap.get(id), snaps }))
    .filter((e): e is { competitor: Competitor; snaps: PricingSnapshot[] } => !!e.competitor)
    .sort((a, b) => a.competitor.name.localeCompare(b.competitor.name))

  return (
    <div>
      <div className="mb-6 flex items-baseline justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-text-bright">Pricing History</h1>
          <p className="text-sm text-text/60 mt-1">
            Per-plan price tracking with intro vs renewal split. Solid lines = today's price; dashed = renewal.
          </p>
        </div>
      </div>

      {competitorEntries.length === 0 ? (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center">
          <p className="text-text/50">No pricing data captured yet.</p>
          <p className="text-sm text-text/40 mt-1">Pricing history will appear here after scans capture pricing pages.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {competitorEntries.map(({ competitor, snaps }) => (
            <CompetitorHistoryCard key={competitor.id} competitor={competitor} snapshots={snaps} />
          ))}
        </div>
      )}
    </div>
  )
}
