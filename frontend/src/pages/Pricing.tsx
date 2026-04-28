import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type PricingSnapshot, type Competitor, type VisionPlan } from '@/api/client'
import { formatDate } from '@/lib/utils'
import { Tag, Clock, AlertTriangle, Image as ImageIcon, X, Star } from 'lucide-react'

function PricingScreenshotButton({ snapshotId }: { snapshotId: string }) {
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
      >
        <ImageIcon size={11} /> Screenshot
      </button>
      {open && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6" onClick={() => setOpen(false)}>
          <button onClick={() => setOpen(false)} className="absolute top-4 right-4 text-white/80 hover:text-white"><X size={24} /></button>
          <div className="max-h-full max-w-4xl overflow-auto bg-bg-card rounded-xl border border-border" onClick={e => e.stopPropagation()}>
            {data?.url ? <img src={data.url} alt="Pricing screenshot" className="block w-full h-auto" />
             : data ? <p className="p-8 text-text/50">Screenshot not available.</p>
             : <p className="p-8 text-text/50">Loading…</p>}
          </div>
        </div>
      )}
    </>
  )
}

function VisionPlanRow({ plan, currency }: { plan: VisionPlan; currency: string }) {
  const intro = plan.intro?.total_price ?? null
  const renewal = plan.renewal?.total_price ?? null
  const cycleLbl = plan.billing_cycle_weeks ? `${plan.billing_cycle_weeks}-week cycle` : null
  return (
    <tr className="border-b border-border/20 last:border-0 hover:bg-bg-hover/50 transition-colors align-top">
      <td className="px-5 py-3">
        <div className="flex items-center gap-1.5 text-text-bright font-medium">
          {plan.display_name}
          {plan.is_most_popular && <Star size={12} className="text-warning fill-warning" />}
        </div>
        {cycleLbl && <p className="text-[11px] text-text/40 mt-0.5">{cycleLbl}</p>}
        {plan.badges && plan.badges.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {plan.badges.slice(0, 4).map((b, i) => (
              <span key={i} className="inline-block text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 bg-bg-hover text-text/60 border border-border/40">{b}</span>
            ))}
          </div>
        )}
      </td>
      <td className="px-5 py-3 text-right font-semibold text-accent whitespace-nowrap">
        {intro !== null ? <>{intro.toFixed(2)} <span className="text-text/40 font-normal">{currency}</span></> : '—'}
        {plan.intro?.per_day_price != null && (
          <p className="text-[11px] text-text/40 font-normal">{plan.intro.per_day_price.toFixed(2)} {currency}/day</p>
        )}
      </td>
      <td className="px-5 py-3 text-right text-text/60 whitespace-nowrap">
        {renewal !== null ? (
          <>
            {renewal.toFixed(2)} <span className="text-text/40">{currency}</span>
            {plan.renewal?.billed_every && <p className="text-[11px] text-text/40">/ {plan.renewal.billed_every}</p>}
          </>
        ) : <span className="text-text/30">—</span>}
      </td>
      <td className="px-5 py-3 text-right text-text/50 whitespace-nowrap">
        {plan.monthly_equivalent !== null && plan.monthly_equivalent !== undefined ? `${plan.monthly_equivalent.toFixed(2)} ${currency}` : '—'}
        {plan.discount_pct ? <p className="text-[11px] text-success">−{plan.discount_pct}%</p> : null}
      </td>
    </tr>
  )
}

function PricingCard({ snapshot, competitor }: { snapshot: PricingSnapshot; competitor: Competitor | undefined }) {
  const vision = snapshot.metadata?.vision
  const currency = vision?.currency ?? snapshot.plans?.find(p => p.currency)?.currency ?? 'USD'

  return (
    <div className="bg-bg-card rounded-xl border border-border overflow-hidden">
      <div className="px-5 py-4 border-b border-border/50 flex items-center justify-between flex-wrap gap-2">
        <div>
          <h3 className="text-text-bright font-medium flex items-center gap-2">
            {competitor?.name ?? 'Unknown'}
            {vision && (
              <span className="text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 border border-success/30 text-success bg-success/5">
                {vision.page_kind ?? 'pricing'} · vision
              </span>
            )}
          </h3>
          <p className="text-xs text-text/50 mt-0.5">{formatDate(snapshot.created_at)}</p>
        </div>
        <div className="flex items-center gap-2">
          {snapshot.captured_at_step && (
            <span className="text-xs text-text/40 bg-bg-hover px-2 py-1 rounded">Step {snapshot.captured_at_step}</span>
          )}
          {snapshot.screenshot_path && <PricingScreenshotButton snapshotId={snapshot.id} />}
        </div>
      </div>

      {vision && vision.plans?.length > 0 ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-text/50 uppercase tracking-wide border-b border-border/30">
              <th className="text-left px-5 py-2.5 font-medium">Plan</th>
              <th className="text-right px-5 py-2.5 font-medium">Today's price</th>
              <th className="text-right px-5 py-2.5 font-medium">Renewal</th>
              <th className="text-right px-5 py-2.5 font-medium">Monthly equiv.</th>
            </tr>
          </thead>
          <tbody>
            {vision.plans.map((plan, i) => (
              <VisionPlanRow key={`${plan.plan_id}-${i}`} plan={plan} currency={currency} />
            ))}
          </tbody>
        </table>
      ) : snapshot.plans && snapshot.plans.length > 0 ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-text/50 uppercase tracking-wide border-b border-border/30">
              <th className="text-left px-5 py-2.5 font-medium">Plan</th>
              <th className="text-right px-5 py-2.5 font-medium">Price</th>
              <th className="text-right px-5 py-2.5 font-medium">Period</th>
              <th className="text-right px-5 py-2.5 font-medium">Features</th>
            </tr>
          </thead>
          <tbody>
            {snapshot.plans.map((plan, i) => (
              <tr key={i} className="border-b border-border/20 last:border-0 hover:bg-bg-hover/50 transition-colors">
                <td className="px-5 py-3 text-text-bright font-medium">{plan.name}</td>
                <td className="px-5 py-3 text-right font-semibold text-accent">
                  {plan.price} <span className="text-text/40 font-normal">{plan.currency}</span>
                </td>
                <td className="px-5 py-3 text-right text-text/60">{plan.period}</td>
                <td className="px-5 py-3 text-right text-text/50">{plan.features?.length ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="px-5 py-4 text-sm text-text/40 italic">No pricing plans captured</p>
      )}

      {((snapshot.discounts && snapshot.discounts.length > 0) || snapshot.trial_info?.has_trial) && (
        <div className="px-5 py-3 border-t border-border/30 flex flex-wrap gap-2">
          {snapshot.discounts?.map((d, i) => (
            <span key={i} className="inline-flex items-center gap-1.5 text-xs text-warning bg-warning/5 border border-warning/15 px-2.5 py-1 rounded-full">
              <Tag size={11} />
              {d.type}{d.amount ? ` — ${d.amount}` : ''}
              {d.original_price && d.discounted_price && (
                <span className="text-text/50 ml-1">
                  <span className="line-through">{d.original_price}</span> → <span className="text-success">{d.discounted_price}</span>
                </span>
              )}
              {d.applies_to_plan_id && <span className="text-text/40 ml-1">on {d.applies_to_plan_id}</span>}
            </span>
          ))}
          {snapshot.trial_info?.has_trial && (
            <span className="inline-flex items-center gap-1.5 text-xs text-info bg-info/5 border border-info/15 px-2.5 py-1 rounded-full">
              <Clock size={11} />
              {snapshot.trial_info.trial_days ?? '?'}-day trial
              {snapshot.trial_info.trial_price && ` · ${snapshot.trial_info.trial_price}`}
              {snapshot.trial_info.renews_at != null && ` → renews ${snapshot.trial_info.renews_at} ${currency}`}
            </span>
          )}
        </div>
      )}

      {vision?.notes && (
        <div className="px-5 py-2 border-t border-border/30 text-[11px] text-text/45 leading-snug">
          <span className="text-text/60 font-medium">Extractor note: </span>{vision.notes}
        </div>
      )}
    </div>
  )
}

function stopReasonLabel(reason: string | null): string {
  switch (reason) {
    case 'funnel_reset': return 'Funnel looped back before reaching pricing'
    case 'max_steps': return 'Hit step limit before reaching pricing'
    case 'timeout': return 'Scan timed out before reaching pricing'
    default: return 'Agent completed without finding a pricing page'
  }
}

export function Pricing() {
  const { data: snapshots, isLoading: loadingPricing } = useQuery({
    queryKey: ['pricing-latest'],
    queryFn: api.latestPricing,
  })

  const { data: competitors } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  const { data: scans } = useQuery({
    queryKey: ['scans'],
    queryFn: () => api.listScans(),
  })

  if (loadingPricing) {
    return <div className="text-text/50 py-12 text-center">Loading...</div>
  }

  const compMap = new Map((competitors ?? []).map(c => [c.id, c]))
  const pricingCompIds = new Set((snapshots ?? []).map(s => s.competitor_id))

  // Find competitors with completed scans but no pricing
  const latestCompletedByCompetitor = new Map<string, { stop_reason: string | null; completed_at: string | null }>()
  for (const scan of scans ?? []) {
    if (scan.status === 'completed' && !latestCompletedByCompetitor.has(scan.competitor_id)) {
      latestCompletedByCompetitor.set(scan.competitor_id, {
        stop_reason: scan.stop_reason,
        completed_at: scan.completed_at,
      })
    }
  }

  const missingPricing = [...latestCompletedByCompetitor.entries()]
    .filter(([compId]) => !pricingCompIds.has(compId))
    .map(([compId, scan]) => ({ competitor: compMap.get(compId), ...scan }))
    .filter(m => m.competitor)

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-text-bright">Pricing</h1>
        <p className="text-sm text-text/60 mt-1">Latest pricing snapshots across competitors</p>
      </div>

      {snapshots && snapshots.length > 0 ? (
        <div className="space-y-4">
          {snapshots.map(s => (
            <PricingCard key={s.id} snapshot={s} competitor={compMap.get(s.competitor_id)} />
          ))}
        </div>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center">
          <p className="text-text/50">No pricing data captured yet.</p>
          <p className="text-sm text-text/40 mt-1">Pricing snapshots will appear here after scans capture pricing pages.</p>
        </div>
      )}

      {missingPricing.length > 0 && (
        <div className="mt-8">
          <h2 className="text-lg font-medium text-text-bright mb-4 flex items-center gap-2">
            <AlertTriangle size={18} className="text-warning" />
            Missing pricing ({missingPricing.length})
          </h2>
          <div className="bg-bg-card rounded-xl border border-warning/20 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-text/50 uppercase tracking-wide border-b border-border/30">
                  <th className="text-left px-5 py-2.5 font-medium">Competitor</th>
                  <th className="text-left px-5 py-2.5 font-medium">Reason</th>
                  <th className="text-right px-5 py-2.5 font-medium">Last Scan</th>
                </tr>
              </thead>
              <tbody>
                {missingPricing.map(m => (
                  <tr key={m.competitor!.id} className="border-b border-border/20 last:border-0">
                    <td className="px-5 py-3 text-text-bright font-medium">{m.competitor!.name}</td>
                    <td className="px-5 py-3 text-warning/80 text-xs">{stopReasonLabel(m.stop_reason)}</td>
                    <td className="px-5 py-3 text-right text-text/40 text-xs">
                      {m.completed_at ? formatDate(m.completed_at) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
