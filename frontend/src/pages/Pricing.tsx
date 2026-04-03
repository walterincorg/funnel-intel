import { useQuery } from '@tanstack/react-query'
import { api, type PricingSnapshot, type Competitor } from '@/api/client'
import { formatDate } from '@/lib/utils'
import { DollarSign, Tag, Clock } from 'lucide-react'

function PricingCard({ snapshot, competitor }: { snapshot: PricingSnapshot; competitor: Competitor | undefined }) {
  return (
    <div className="bg-bg-card rounded-xl border border-border p-5">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="text-text-bright font-medium">{competitor?.name ?? 'Unknown'}</h3>
          <p className="text-xs text-text/50 mt-0.5">{formatDate(snapshot.created_at)}</p>
        </div>
        {snapshot.captured_at_step && (
          <span className="text-xs text-text/40">Step {snapshot.captured_at_step}</span>
        )}
      </div>

      {/* Plans */}
      {snapshot.plans && snapshot.plans.length > 0 ? (
        <div className="space-y-2 mb-4">
          <p className="text-xs text-text/60 uppercase tracking-wide flex items-center gap-1">
            <DollarSign size={12} /> Plans
          </p>
          <div className="grid gap-2">
            {snapshot.plans.map((plan, i) => (
              <div key={i} className="bg-bg/50 rounded-lg p-3 border border-border/50">
                <div className="flex items-baseline justify-between">
                  <span className="text-sm text-text-bright font-medium">{plan.name}</span>
                  <span className="text-lg font-semibold text-accent">
                    {plan.price} <span className="text-xs text-text/50">{plan.currency}/{plan.period}</span>
                  </span>
                </div>
                {plan.features && plan.features.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {plan.features.map((f, j) => (
                      <span key={j} className="text-xs text-text/50 bg-bg-hover px-2 py-0.5 rounded">{f}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-sm text-text/40 italic mb-4">No pricing plans captured</p>
      )}

      {/* Discounts */}
      {snapshot.discounts && snapshot.discounts.length > 0 && (
        <div className="space-y-2 mb-4">
          <p className="text-xs text-text/60 uppercase tracking-wide flex items-center gap-1">
            <Tag size={12} /> Discounts
          </p>
          {snapshot.discounts.map((d, i) => (
            <div key={i} className="bg-warning/5 border border-warning/20 rounded-lg p-3">
              <div className="flex items-baseline justify-between">
                <span className="text-sm text-warning font-medium">{d.type} — {d.amount}</span>
              </div>
              {d.original_price && d.discounted_price && (
                <p className="text-xs text-text/60 mt-1">
                  <span className="line-through">{d.original_price}</span> → <span className="text-success">{d.discounted_price}</span>
                </p>
              )}
              {d.conditions && (
                <p className="text-xs text-text/40 mt-1">{d.conditions}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Trial */}
      {snapshot.trial_info && snapshot.trial_info.has_trial && (
        <div className="bg-info/5 border border-info/20 rounded-lg p-3">
          <p className="text-xs text-info font-medium flex items-center gap-1">
            <Clock size={12} /> Free Trial
          </p>
          <p className="text-sm text-text-bright mt-1">
            {snapshot.trial_info.trial_days} days
            {snapshot.trial_info.trial_price && ` — then ${snapshot.trial_info.trial_price}`}
          </p>
        </div>
      )}
    </div>
  )
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

  if (loadingPricing) {
    return <div className="text-text/50 py-12 text-center">Loading...</div>
  }

  const compMap = new Map((competitors ?? []).map(c => [c.id, c]))

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-text-bright">Pricing</h1>
        <p className="text-sm text-text/60 mt-1">Latest pricing snapshots across competitors</p>
      </div>

      {snapshots && snapshots.length > 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
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
    </div>
  )
}
