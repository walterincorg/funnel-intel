import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Sparkles, ExternalLink, Users } from 'lucide-react'
import { api, type BuiltWithRelationship } from '@/api/client'
import { cn, checkActive, getPrevRunCutoff } from '@/lib/utils'

function isNew(r: BuiltWithRelationship, prevRunAt: string | null): boolean {
  return !!r.first_seen_at && !!prevRunAt && new Date(r.first_seen_at) > new Date(prevRunAt)
}

// A single related domain inside a cluster.
interface RelatedEntry {
  relatedDomain: string
  attributes: Set<string>
  firstDetected: string | null
  lastDetected: string | null
  rows: BuiltWithRelationship[]
  hasNew: boolean
  active: boolean
}

// A group of competitors that all share at least one related domain.
interface Cluster {
  competitors: string[] // sorted
  related: RelatedEntry[]
  newCount: number
  activeCount: number
  allInactive: boolean
}

function buildClusters(rows: BuiltWithRelationship[], prevRunAt: string | null): Cluster[] {
  // Step 1: group rows by related_domain, collecting which competitors link to it.
  const byRelated = new Map<string, RelatedEntry>()
  for (const r of rows) {
    let entry = byRelated.get(r.related_domain)
    if (!entry) {
      entry = {
        relatedDomain: r.related_domain,
        attributes: new Set(),
        firstDetected: null,
        lastDetected: null,
        rows: [],
        hasNew: false,
        active: false,
      }
      byRelated.set(r.related_domain, entry)
    }
    entry.rows.push(r)
    if (r.attribute_value) entry.attributes.add(r.attribute_value)
    if (r.first_detected && (!entry.firstDetected || r.first_detected < entry.firstDetected)) {
      entry.firstDetected = r.first_detected
    }
    if (r.last_detected && (!entry.lastDetected || r.last_detected > entry.lastDetected)) {
      entry.lastDetected = r.last_detected
    }
    if (isNew(r, prevRunAt)) entry.hasNew = true
    if (checkActive(r.last_detected)) entry.active = true
  }

  // Step 2: bucket related-entries by the exact set of competitors that link to them.
  const clusters = new Map<string, Cluster>()
  for (const entry of byRelated.values()) {
    const competitorSet = [...new Set(entry.rows.map(r => r.source_domain))].sort()
    const key = competitorSet.join('|')
    let cluster = clusters.get(key)
    if (!cluster) {
      cluster = {
        competitors: competitorSet,
        related: [],
        newCount: 0,
        activeCount: 0,
        allInactive: true,
      }
      clusters.set(key, cluster)
    }
    cluster.related.push(entry)
    if (entry.hasNew) cluster.newCount += 1
    if (entry.active) {
      cluster.activeCount += 1
      cluster.allInactive = false
    }
  }

  // Sort clusters: multi-competitor first, then by #related desc, then alphabetic.
  return [...clusters.values()].sort((a, b) => {
    if ((a.competitors.length > 1) !== (b.competitors.length > 1)) {
      return b.competitors.length - a.competitors.length
    }
    if (a.competitors.length !== b.competitors.length) {
      return b.competitors.length - a.competitors.length
    }
    if (a.related.length !== b.related.length) return b.related.length - a.related.length
    return a.competitors[0].localeCompare(b.competitors[0])
  })
}

function ClusterCard({
  cluster,
  activeOnly,
  prevRunAt,
}: {
  cluster: Cluster
  activeOnly: boolean
  prevRunAt: string | null
}) {
  const [open, setOpen] = useState(false)
  const visibleRelated = useMemo(
    () => (activeOnly ? cluster.related.filter(r => r.active) : cluster.related),
    [cluster.related, activeOnly],
  )
  const isShared = cluster.competitors.length > 1
  const displayCount = activeOnly ? cluster.activeCount : cluster.related.length

  return (
    <div
      className={cn(
        'bg-bg-card border rounded-xl overflow-hidden transition-colors',
        cluster.allInactive ? 'border-border/40 opacity-70' : 'border-border',
      )}
    >
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-start justify-between gap-4 p-5 text-left hover:bg-bg-hover/30 transition-colors"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center flex-wrap gap-1.5 mb-2">
            {isShared && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-warning/10 text-warning">
                <Users size={11} />
                {cluster.competitors.length} competitors share
              </span>
            )}
            {cluster.newCount > 0 && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-info/10 text-info">
                <Sparkles size={11} />
                {cluster.newCount} new
              </span>
            )}
            {cluster.allInactive && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-text/5 text-text/40">inactive</span>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {cluster.competitors.map(c => (
              <span
                key={c}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-mono bg-bg-hover/70 text-text-bright border border-border/50"
              >
                {c}
              </span>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <div className="text-right">
            <div className="text-lg font-semibold text-text-bright">{displayCount}</div>
            <div className="text-xs text-text/50">related domain{displayCount !== 1 ? 's' : ''}</div>
          </div>
          <ChevronDown
            size={18}
            className={cn('text-text/40 transition-transform', open && 'rotate-180')}
          />
        </div>
      </button>

      {open && (
        <div className="border-t border-border/40">
          {visibleRelated.length === 0 ? (
            <p className="p-5 text-sm text-text/40 italic">No active related domains.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-bg-hover/30">
                  <tr className="text-xs text-text/50 uppercase tracking-wide">
                    <th className="text-left px-5 py-2">Related domain</th>
                    <th className="text-left px-5 py-2">Shared attributes</th>
                    <th className="text-left px-5 py-2">First</th>
                    <th className="text-left px-5 py-2">Last</th>
                    <th className="text-right px-5 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRelated.map(r => (
                    <tr key={r.relatedDomain} className="border-t border-border/30 hover:bg-bg-hover/20">
                      <td className="px-5 py-2">
                        <a
                          href={`https://${r.relatedDomain}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 font-mono text-accent hover:underline"
                        >
                          {r.relatedDomain}
                          <ExternalLink size={10} />
                        </a>
                      </td>
                      <td className="px-5 py-2">
                        <div className="flex flex-wrap gap-1">
                          {[...r.attributes].slice(0, 3).map(a => (
                            <span
                              key={a}
                              className="px-1.5 py-0.5 rounded text-[11px] font-mono bg-accent-dim/40 text-accent border border-accent/20"
                            >
                              {a}
                            </span>
                          ))}
                          {r.attributes.size > 3 && (
                            <span className="px-1.5 py-0.5 text-[11px] text-text/40">
                              +{r.attributes.size - 3}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-5 py-2 text-text/60">{r.firstDetected ?? '—'}</td>
                      <td className="px-5 py-2 text-text/60">{r.lastDetected ?? '—'}</td>
                      <td className="px-5 py-2 text-right">
                        {r.hasNew && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] font-medium bg-info/10 text-info">
                            <Sparkles size={9} />
                            new
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function DomainIntel() {
  const queryClient = useQueryClient()
  const [activeOnly, setActiveOnly] = useState(true)

  const { data: runs } = useQuery({
    queryKey: ['domain-runs'],
    queryFn: api.domainRuns,
    refetchInterval: 3000,
  })

  const { data: relationships, isLoading } = useQuery({
    queryKey: ['bw-relationships'],
    queryFn: () => api.listRelationships(),
  })

  const isScanning = runs?.[0]?.status === 'running' || runs?.[0]?.status === 'pending'

  const scanMutation = useMutation({
    mutationFn: api.triggerDomainScan,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domain'] }),
  })

  const prevRunAt = getPrevRunCutoff(runs ?? [])

  const allClusters = useMemo(
    () => buildClusters(relationships ?? [], prevRunAt),
    [relationships, prevRunAt],
  )
  const visibleClusters = activeOnly
    ? allClusters.filter(c => !c.allInactive)
    : allClusters

  const totalNew = useMemo(
    () =>
      (relationships ?? []).filter(
        r => checkActive(r.last_detected) && isNew(r, prevRunAt),
      ).length,
    [relationships, prevRunAt],
  )

  const sharedClusters = visibleClusters.filter(c => c.competitors.length > 1).length

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-semibold text-text-bright">Domain Intelligence</h1>
        <button
          onClick={() => scanMutation.mutate()}
          disabled={scanMutation.isPending || isScanning}
          className="px-4 py-2 text-sm font-medium rounded-lg bg-accent text-white hover:bg-accent/90 disabled:opacity-50"
        >
          {isScanning ? 'Scanning...' : 'Re-scan All'}
        </button>
      </div>
      <p className="text-sm text-text/60 mb-6">
        Competitors grouped by the domains they share tracking attributes with. Multi-competitor
        clusters are likely the same operator or a shared advertising network.
      </p>

      {totalNew > 0 && (
        <div className="flex items-center gap-2 p-3 mb-6 rounded-lg bg-info/5 border border-info/20">
          <Sparkles size={15} className="text-info shrink-0" />
          <p className="text-sm text-info">
            <span className="font-semibold">{totalNew} new row{totalNew !== 1 ? 's' : ''}</span>{' '}
            appeared in the latest scan.
          </p>
        </div>
      )}

      {!isLoading && allClusters.length > 0 && (
        <div className="flex items-center justify-between mb-4 text-sm text-text/60">
          <div className="flex items-center gap-4">
            <span>
              {visibleClusters.length} cluster{visibleClusters.length !== 1 ? 's' : ''}
            </span>
            {sharedClusters > 0 && (
              <span className="text-warning">
                {sharedClusters} multi-competitor
              </span>
            )}
          </div>
          <label className="flex items-center gap-2 text-xs text-text/60 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={e => setActiveOnly(e.target.checked)}
              className="rounded"
            />
            Active only
          </label>
        </div>
      )}

      {isLoading && <p className="text-text/50 text-sm">Loading relationships...</p>}

      {!isLoading && allClusters.length === 0 && (
        <div className="text-center py-16 text-text/50">
          <p className="text-lg font-medium mb-2">No relationship data yet</p>
          <p className="text-sm">Click "Re-scan All" to scrape BuiltWith for connected domains.</p>
        </div>
      )}

      {!isLoading && visibleClusters.length > 0 && (
        <div className="space-y-3">
          {visibleClusters.map(c => (
            <ClusterCard
              key={c.competitors.join('|')}
              cluster={c}
              activeOnly={activeOnly}
              prevRunAt={prevRunAt}
            />
          ))}
        </div>
      )}
    </div>
  )
}
