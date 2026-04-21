import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type PricingSnapshot, type Competitor } from '@/api/client'
import { formatDate } from '@/lib/utils'
import { TrendingUp, TrendingDown, Plus, Minus, Clock } from 'lucide-react'

// --- Price parsing ---

function parsePrice(raw: string | undefined | null): number | null {
  if (!raw) return null
  const n = parseFloat(raw.replace(/[^0-9.]/g, ''))
  return isNaN(n) ? null : n
}

// --- Change detection ---

type ChangeKind = 'increased' | 'decreased' | 'added' | 'removed'

interface PriceChange {
  snapshotIndex: number
  date: string
  planName: string
  kind: ChangeKind
  oldPrice: string | null
  newPrice: string | null
  currency: string
}

function detectChanges(snapshots: PricingSnapshot[]): PriceChange[] {
  const changes: PriceChange[] = []
  for (let i = 1; i < snapshots.length; i++) {
    const prev = snapshots[i - 1]
    const curr = snapshots[i]
    const prevPlans = prev.plans ?? []
    const currPlans = curr.plans ?? []

    // Skip transition if previous snapshot had no plans — it's a first-detection event, not a change
    if (prevPlans.length === 0) continue

    const prevMap = new Map(prevPlans.map(p => [p.name, p]))
    const currMap = new Map(currPlans.map(p => [p.name, p]))

    // Collect raw adds and removes
    const rawAdded: PriceChange[] = []
    const rawRemoved: PriceChange[] = []

    for (const [name, currPlan] of currMap) {
      const prevPlan = prevMap.get(name)
      if (!prevPlan) {
        rawAdded.push({ snapshotIndex: i, date: curr.created_at, planName: name, kind: 'added', oldPrice: null, newPrice: currPlan.price, currency: currPlan.currency })
      } else {
        const pv = parsePrice(prevPlan.price)
        const cv = parsePrice(currPlan.price)
        if (pv !== null && cv !== null && pv !== cv) {
          const kind: ChangeKind = cv > pv ? 'increased' : 'decreased'
          changes.push({ snapshotIndex: i, date: curr.created_at, planName: name, kind, oldPrice: prevPlan.price, newPrice: currPlan.price, currency: currPlan.currency })
        }
      }
    }
    for (const [name, prevPlan] of prevMap) {
      if (!currMap.has(name)) {
        rawRemoved.push({ snapshotIndex: i, date: curr.created_at, planName: name, kind: 'removed', oldPrice: prevPlan.price, newPrice: null, currency: prevPlan.currency })
      }
    }

    // Suppress rename noise: if a plan was removed and another added at the same price, it's just a rename
    const usedAdded = new Set<number>()
    for (const rem of rawRemoved) {
      const remPrice = parsePrice(rem.oldPrice)
      const matchIdx = rawAdded.findIndex((add, idx) => !usedAdded.has(idx) && parsePrice(add.newPrice) === remPrice)
      if (matchIdx !== -1) {
        usedAdded.add(matchIdx) // matched — suppress both
      } else {
        changes.push(rem)
      }
    }
    for (let j = 0; j < rawAdded.length; j++) {
      if (!usedAdded.has(j)) changes.push(rawAdded[j])
    }
  }
  return changes
}

// --- SVG Line Chart ---

const PLAN_COLORS = ['#818cf8', '#34d399', '#fbbf24', '#60a5fa', '#f87171', '#c084fc']
const CHART_W = 600
const CHART_AREA_H = 120  // height of just the plotting area + axes
const PAD = { top: 8, right: 20, bottom: 28, left: 44 }
const LEGEND_ROW_H = 14
const LEGEND_COLS = 3

// Trace plan identity across renames so the chart shows continuous lines
// instead of fragmenting into one series per historical name.
function buildMergedSeries(snapshots: PricingSnapshot[]): { name: string; values: (number | null)[] }[] {
  if (snapshots.length === 0) return []

  type Identity = { name: string; values: (number | null)[] }
  const identities: Identity[] = []
  // maps current-snapshot plan name → identity
  let nameToId = new Map<string, Identity>()

  for (const plan of (snapshots[0].plans ?? [])) {
    const id: Identity = { name: plan.name, values: [parsePrice(plan.price)] }
    identities.push(id)
    nameToId.set(plan.name, id)
  }

  for (let i = 1; i < snapshots.length; i++) {
    const prevPlans = snapshots[i - 1].plans ?? []
    const currPlans = snapshots[i].plans ?? []
    const prevMap = new Map(prevPlans.map(p => [p.name, p]))
    const currMap = new Map(currPlans.map(p => [p.name, p]))

    const nextNameToId = new Map<string, Identity>()
    const matched = new Set<Identity>()

    // Pass 1: exact name match (name unchanged, just update price)
    for (const [name, currPlan] of currMap) {
      if (prevMap.has(name)) {
        const id = nameToId.get(name)
        if (id) {
          id.values.push(parsePrice(currPlan.price))
          id.name = name
          nextNameToId.set(name, id)
          matched.add(id)
        }
      }
    }

    // Pass 2: rename detection — unmatched prev + unmatched curr at same price
    const unmatchedPrev = [...prevMap.entries()].filter(([n]) => !currMap.has(n))
    const unmatchedCurr = [...currMap.entries()].filter(([n]) => !prevMap.has(n) && !nextNameToId.has(n))

    for (const [prevName] of unmatchedPrev) {
      const prevPrice = parsePrice(prevMap.get(prevName)?.price)
      if (prevPrice === null) continue
      const idx = unmatchedCurr.findIndex(([, cp]) => parsePrice(cp.price) === prevPrice)
      if (idx !== -1) {
        const [currName, currPlan] = unmatchedCurr.splice(idx, 1)[0]
        const id = nameToId.get(prevName)
        if (id) {
          id.values.push(parsePrice(currPlan.price))
          id.name = currName  // update to latest name
          nextNameToId.set(currName, id)
          matched.add(id)
        }
      }
    }

    // Push null for identities absent this snapshot
    for (const id of identities) {
      if (!matched.has(id) && id.values.length === i) id.values.push(null)
    }

    // New plans with no prior identity
    for (const [currName, currPlan] of currMap) {
      if (!nextNameToId.has(currName)) {
        const id: Identity = { name: currName, values: new Array(i).fill(null) }
        id.values.push(parsePrice(currPlan.price))
        identities.push(id)
        nextNameToId.set(currName, id)
      }
    }

    nameToId = nextNameToId
  }

  return identities.filter(id => id.values.some(v => v !== null))
}

function PriceChart({ snapshots, changedIndices }: { snapshots: PricingSnapshot[]; changedIndices: Set<number> }) {
  if (snapshots.length < 2) return null

  const series = buildMergedSeries(snapshots)
  if (series.length === 0) return null

  const allValues = series.flatMap(s => s.values).filter((v): v is number => v !== null)
  const minVal = Math.min(...allValues)
  const maxVal = Math.max(...allValues)
  const valRange = maxVal - minVal || 1

  const innerW = CHART_W - PAD.left - PAD.right
  const innerH = CHART_AREA_H - PAD.top - PAD.bottom

  const xPos = (i: number) => PAD.left + (i / (snapshots.length - 1)) * innerW
  const yPos = (v: number) => PAD.top + innerH - ((v - minVal) / valRange) * innerH

  // X axis date labels (show first, last, and every ~4th)
  const labelIndices = new Set<number>([0, snapshots.length - 1])
  const step = Math.max(1, Math.floor(snapshots.length / 4))
  for (let i = step; i < snapshots.length - 1; i += step) labelIndices.add(i)

  // For each labeled index: show "Apr 21" on the first label for that date,
  // then just the time for subsequent same-day labels.
  const sortedLabelIndices = [...labelIndices].sort((a, b) => a - b)
  const seenDates = new Set<string>()
  const xLabels = new Map<number, string>()
  for (const i of sortedLabelIndices) {
    const d = new Date(snapshots[i].created_at)
    const dateStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    if (!seenDates.has(dateStr)) {
      seenDates.add(dateStr)
      xLabels.set(i, dateStr)
    } else {
      xLabels.set(i, d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }))
    }
  }

  // Legend layout
  const legendRows = Math.ceil(series.length / LEGEND_COLS)
  const legendH = legendRows > 0 ? legendRows * LEGEND_ROW_H + 6 : 0
  const totalH = CHART_AREA_H + legendH
  const colW = (CHART_W - PAD.left) / LEGEND_COLS

  return (
    <svg viewBox={`0 0 ${CHART_W} ${totalH}`} width="100%" className="block">
      {/* Y axis gridlines */}
      {[0, 0.25, 0.5, 0.75, 1].map(t => {
        const y = PAD.top + innerH * (1 - t)
        const label = '$' + (minVal + valRange * t).toFixed(0)
        return (
          <g key={t}>
            <line x1={PAD.left} y1={y} x2={CHART_W - PAD.right} y2={y} stroke="#2e303a" strokeWidth="1" />
            <text x={PAD.left - 6} y={y + 4} textAnchor="end" fontSize="9" fill="#6b7280">{label}</text>
          </g>
        )
      })}

      {/* X axis: date on first label of each day, time on subsequent same-day labels */}
      {sortedLabelIndices.map(i => (
        <text key={i} x={xPos(i)} y={CHART_AREA_H - 4} textAnchor="middle" fontSize="9" fill="#6b7280">
          {xLabels.get(i)}
        </text>
      ))}

      {/* Change markers */}
      {[...changedIndices].map(i => (
        <line
          key={i}
          x1={xPos(i)} y1={PAD.top}
          x2={xPos(i)} y2={PAD.top + innerH}
          stroke="#fbbf24" strokeWidth="1" strokeDasharray="3,2" opacity="0.4"
        />
      ))}

      {/* Lines + dots per plan */}
      {series.map((s, si) => {
        const color = PLAN_COLORS[si % PLAN_COLORS.length]
        const points: { x: number; y: number; i: number; v: number }[] = []
        s.values.forEach((v, i) => { if (v !== null) points.push({ x: xPos(i), y: yPos(v), i, v }) })

        const pathD = points.map((p, pi) => `${pi === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')

        return (
          <g key={s.name}>
            <path d={pathD} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
            {points.map(p => (
              <circle
                key={p.i}
                cx={p.x} cy={p.y} r={changedIndices.has(p.i) ? 4 : 2.5}
                fill={changedIndices.has(p.i) ? color : '#1a1b23'}
                stroke={color}
                strokeWidth={changedIndices.has(p.i) ? 1.5 : 1}
              >
                <title>{s.name}: {p.v} · {new Date(snapshots[p.i].created_at).toLocaleDateString()}</title>
              </circle>
            ))}
          </g>
        )
      })}

      {/* Legend — below chart, 3 columns, truncated names */}
      {series.map((s, si) => {
        const col = si % LEGEND_COLS
        const row = Math.floor(si / LEGEND_COLS)
        const truncated = s.name.length > 18 ? s.name.slice(0, 17) + '…' : s.name
        return (
          <g key={s.name} transform={`translate(${PAD.left + col * colW}, ${CHART_AREA_H + 4 + row * LEGEND_ROW_H})`}>
            <line x1="0" y1="5" x2="10" y2="5" stroke={PLAN_COLORS[si % PLAN_COLORS.length]} strokeWidth="1.5" />
            <circle cx="5" cy="5" r="2" fill={PLAN_COLORS[si % PLAN_COLORS.length]} />
            <text x="14" y="9" fontSize="9" fill="#9ca3af">{truncated}</text>
          </g>
        )
      })}
    </svg>
  )
}

// --- Change badge ---

function ChangeBadge({ kind }: { kind: ChangeKind }) {
  if (kind === 'increased') return (
    <span className="inline-flex items-center gap-1 text-xs text-danger bg-danger/10 border border-danger/20 px-2 py-0.5 rounded-full">
      <TrendingUp size={10} /> Higher
    </span>
  )
  if (kind === 'decreased') return (
    <span className="inline-flex items-center gap-1 text-xs text-success bg-success/10 border border-success/20 px-2 py-0.5 rounded-full">
      <TrendingDown size={10} /> Lower
    </span>
  )
  if (kind === 'added') return (
    <span className="inline-flex items-center gap-1 text-xs text-info bg-info/10 border border-info/20 px-2 py-0.5 rounded-full">
      <Plus size={10} /> New plan
    </span>
  )
  return (
    <span className="inline-flex items-center gap-1 text-xs text-text/50 bg-bg-hover border border-border px-2 py-0.5 rounded-full">
      <Minus size={10} /> Removed
    </span>
  )
}

// --- Per-competitor card ---

function CompetitorHistoryCard({ competitor, snapshots }: { competitor: Competitor; snapshots: PricingSnapshot[] }) {
  const [priceChangesOnly, setPriceChangesOnly] = useState(true)

  // Sort ascending for timeline
  const sorted = [...snapshots].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
  const changes = detectChanges(sorted)
  const changedIndices = new Set(changes.map(c => c.snapshotIndex))

  const priceChanges = changes.filter(c => c.kind === 'increased' || c.kind === 'decreased')
  const displayedChanges = priceChangesOnly ? priceChanges : changes
  const hasStructuralChanges = changes.some(c => c.kind === 'added' || c.kind === 'removed')

  const lastChange = changes.length > 0 ? changes[changes.length - 1] : null
  const lastChangeDate = lastChange
    ? new Date(lastChange.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : null

  return (
    <div className="bg-bg-card rounded-xl border border-border overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-border/50 flex items-center justify-between flex-wrap gap-2">
        <div>
          <h3 className="text-text-bright font-medium">{competitor.name}</h3>
          <p className="text-xs text-text/50 mt-0.5">{sorted.length} scan{sorted.length !== 1 ? 's' : ''} captured</p>
        </div>
        <div className="flex items-center gap-2">
          {changes.length > 0 && lastChangeDate !== null ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-warning bg-warning/8 border border-warning/20 px-2.5 py-1 rounded-full">
              <Clock size={11} />
              Last change {lastChangeDate}
            </span>
          ) : sorted.length > 1 ? (
            <span className="text-xs text-success bg-success/8 border border-success/20 px-2.5 py-1 rounded-full">No changes detected</span>
          ) : null}
        </div>
      </div>

      {sorted.length < 2 ? (
        <p className="px-5 py-6 text-sm text-text/40 italic text-center">
          Only 1 scan captured — check back after the next scan to see history
        </p>
      ) : (
        <>
          {/* Chart */}
          <div className="px-5 pt-4 pb-2">
            <PriceChart snapshots={sorted} changedIndices={changedIndices} />
          </div>

          {/* Change log */}
          {changes.length > 0 ? (
            <div className="border-t border-border/30">
              <div className="px-5 py-2 flex items-center justify-between">
                <span className="text-xs text-text/40 uppercase tracking-wide font-medium">Change log</span>
                {hasStructuralChanges && (
                  <button
                    onClick={() => setPriceChangesOnly(v => !v)}
                    className="text-xs text-text/40 hover:text-text/70 transition-colors"
                  >
                    {priceChangesOnly ? 'Show all changes' : 'Price changes only'}
                  </button>
                )}
              </div>
              {displayedChanges.length === 0 ? (
                <p className="px-5 py-3 text-sm text-text/40 border-t border-border/20">No price changes detected.</p>
              ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-text/40 uppercase tracking-wide border-b border-border/20">
                    <th className="text-left px-5 py-2 font-medium">Date</th>
                    <th className="text-left px-5 py-2 font-medium">Plan</th>
                    <th className="text-left px-5 py-2 font-medium">Change</th>
                    <th className="text-right px-5 py-2 font-medium">Price</th>
                  </tr>
                </thead>
                <tbody>
                  {[...displayedChanges].reverse().map((c, i) => (
                    <tr key={i} className="border-b border-border/15 last:border-0 hover:bg-bg-hover/40 transition-colors">
                      <td className="px-5 py-2.5 text-text/50 text-xs whitespace-nowrap">{formatDate(c.date)}</td>
                      <td className="px-5 py-2.5 text-text-bright font-medium">{c.planName}</td>
                      <td className="px-5 py-2.5"><ChangeBadge kind={c.kind} /></td>
                      <td className="px-5 py-2.5 text-right text-xs text-text/60">
                        {c.oldPrice && c.newPrice ? (
                          <>
                            <span className="line-through text-text/30">{c.oldPrice}</span>
                            {' → '}
                            <span className={c.kind === 'increased' ? 'text-danger' : 'text-success'}>{c.newPrice}</span>
                            {' '}
                            <span className="text-text/30">{c.currency}</span>
                          </>
                        ) : c.newPrice ? (
                          <span className="text-info">{c.newPrice} {c.currency}</span>
                        ) : (
                          <span className="text-text/30">{c.oldPrice} {c.currency}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              )}
            </div>
          ) : (
            <p className="px-5 py-3 text-sm text-text/40 border-t border-border/20">No price changes across {sorted.length} scans.</p>
          )}
        </>
      )}
    </div>
  )
}

// --- Page ---

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

  // Group snapshots by competitor
  const byCompetitor = new Map<string, PricingSnapshot[]>()
  for (const s of snapshots ?? []) {
    const list = byCompetitor.get(s.competitor_id) ?? []
    list.push(s)
    byCompetitor.set(s.competitor_id, list)
  }

  // Sort competitors by name
  const competitorEntries = Array.from(byCompetitor.entries())
    .map(([id, snaps]) => ({ competitor: compMap.get(id), snaps }))
    .filter((e): e is { competitor: Competitor; snaps: PricingSnapshot[] } => !!e.competitor)
    .sort((a, b) => a.competitor.name.localeCompare(b.competitor.name))

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-text-bright">Pricing History</h1>
        <p className="text-sm text-text/60 mt-1">Track how competitor pricing has changed over time</p>
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
