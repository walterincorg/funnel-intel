import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Sparkles, ExternalLink, Users } from 'lucide-react'
import { api, type BuiltWithRelationship } from '@/api/client'
import { cn, getPrevRunCutoff } from '@/lib/utils'

function isNew(r: BuiltWithRelationship, prevRunAt: string | null): boolean {
  return !!r.first_seen_at && !!prevRunAt && new Date(r.first_seen_at) > new Date(prevRunAt)
}

// BuiltWith reports month-only strings like "Mar 2026" or "Nov 2024".
// Parse as a Date for comparisons; null / unparseable sort to the bottom.
function parseBwDate(s: string | null): number {
  if (!s) return -Infinity
  const t = new Date(s).getTime()
  return Number.isNaN(t) ? -Infinity : t
}

// A single related domain inside a cluster.
interface RelatedEntry {
  relatedDomain: string
  attributes: Set<string>
  firstDetected: string | null
  lastDetected: string | null
  rows: BuiltWithRelationship[]
  hasNew: boolean
}

// A group of competitors that all share at least one related domain.
interface Cluster {
  competitors: string[]
  related: RelatedEntry[]
  newCount: number
}

// Union-find. Two domains end up in the same component if there's any
// chain of (source <-> related) edges connecting them in the raw data.
class DSU {
  private parent = new Map<string, string>()
  find(x: string): string {
    if (!this.parent.has(x)) {
      this.parent.set(x, x)
      return x
    }
    let root = x
    while (this.parent.get(root)! !== root) root = this.parent.get(root)!
    let cur = x
    while (this.parent.get(cur)! !== root) {
      const next = this.parent.get(cur)!
      this.parent.set(cur, root)
      cur = next
    }
    return root
  }
  union(a: string, b: string) {
    const ra = this.find(a), rb = this.find(b)
    if (ra !== rb) this.parent.set(ra, rb)
  }
}

function competitorDomain(funnelUrl: string | null | undefined): string | null {
  if (!funnelUrl) return null
  try {
    return new URL(funnelUrl).hostname
  } catch {
    return null
  }
}

function buildClusters(
  rows: BuiltWithRelationship[],
  prevRunAt: string | null,
  trackedCompetitors: string[],
): Cluster[] {
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
      }
      byRelated.set(r.related_domain, entry)
    }
    entry.rows.push(r)
    if (r.attribute_value) entry.attributes.add(r.attribute_value)
    if (r.first_detected) {
      const cur = entry.firstDetected
      if (cur === null || parseBwDate(r.first_detected) < parseBwDate(cur)) {
        entry.firstDetected = r.first_detected
      }
    }
    if (r.last_detected && parseBwDate(r.last_detected) > parseBwDate(entry.lastDetected)) {
      entry.lastDetected = r.last_detected
    }
    if (isNew(r, prevRunAt)) entry.hasNew = true
  }

  const dsu = new DSU()
  const competitorSet = new Set<string>(trackedCompetitors)
  for (const c of trackedCompetitors) dsu.find(c) // pre-register so singletons appear
  for (const r of rows) {
    competitorSet.add(r.source_domain)
    dsu.union(r.source_domain, r.related_domain)
  }

  const clusters = new Map<string, Cluster>()
  for (const entry of byRelated.values()) {
    const root = dsu.find(entry.relatedDomain)
    let cluster = clusters.get(root)
    if (!cluster) {
      cluster = { competitors: [], related: [], newCount: 0 }
      clusters.set(root, cluster)
    }
    cluster.related.push(entry)
    if (entry.hasNew) cluster.newCount += 1
  }

  // Attach every tracked competitor to its cluster, creating empty clusters
  // for competitors that returned no BuiltWith relationship data.
  for (const c of competitorSet) {
    const root = dsu.find(c)
    let cluster = clusters.get(root)
    if (!cluster) {
      cluster = { competitors: [], related: [], newCount: 0 }
      clusters.set(root, cluster)
    }
    cluster.competitors.push(c)
  }
  for (const cluster of clusters.values()) {
    cluster.competitors.sort()
    cluster.related.sort(
      (a, b) => parseBwDate(b.lastDetected) - parseBwDate(a.lastDetected),
    )
  }

  return [...clusters.values()].sort((a, b) => {
    if ((a.competitors.length > 1) !== (b.competitors.length > 1)) {
      return b.competitors.length - a.competitors.length
    }
    if (a.competitors.length !== b.competitors.length) {
      return b.competitors.length - a.competitors.length
    }
    if (a.related.length !== b.related.length) return b.related.length - a.related.length
    return (a.competitors[0] ?? '').localeCompare(b.competitors[0] ?? '')
  })
}

function ClusterCard({ cluster }: { cluster: Cluster }) {
  const [open, setOpen] = useState(false)
  const isShared = cluster.competitors.length > 1

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
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
            <div className="text-lg font-semibold text-text-bright">{cluster.related.length}</div>
            <div className="text-xs text-text/50">
              related domain{cluster.related.length !== 1 ? 's' : ''}
            </div>
          </div>
          <ChevronDown
            size={18}
            className={cn('text-text/40 transition-transform', open && 'rotate-180')}
          />
        </div>
      </button>

      {open && cluster.related.length === 0 && (
        <div className="border-t border-border/40 px-5 py-4 text-sm text-text/50 italic">
          BuiltWith returned no relationship data for this competitor — likely no shared
          tracking attributes detected, or the domain is excluded from BuiltWith lookups.
        </div>
      )}

      {open && cluster.related.length > 0 && (
        <div className="border-t border-border/40 overflow-x-auto">
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
              {cluster.related.map(r => (
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
  )
}

export function DomainIntel() {
  const queryClient = useQueryClient()

  const { data: runs } = useQuery({
    queryKey: ['domain-runs'],
    queryFn: api.domainRuns,
    refetchInterval: 3000,
  })

  const { data: relationships, isLoading } = useQuery({
    queryKey: ['bw-relationships'],
    queryFn: () => api.listRelationships(),
  })

  const { data: competitors } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  const isScanning = runs?.[0]?.status === 'running' || runs?.[0]?.status === 'pending'

  const scanMutation = useMutation({
    mutationFn: api.triggerDomainScan,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domain'] }),
  })

  const prevRunAt = getPrevRunCutoff(runs ?? [])

  const trackedDomains = useMemo(
    () =>
      (competitors ?? [])
        .map(c => competitorDomain(c.funnel_url))
        .filter((d): d is string => !!d),
    [competitors],
  )

  const clusters = useMemo(
    () => buildClusters(relationships ?? [], prevRunAt, trackedDomains),
    [relationships, prevRunAt, trackedDomains],
  )

  const totalNew = useMemo(
    () => (relationships ?? []).filter(r => isNew(r, prevRunAt)).length,
    [relationships, prevRunAt],
  )

  const sharedClusters = clusters.filter(c => c.competitors.length > 1).length

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
            <span className="font-semibold">
              {totalNew} new row{totalNew !== 1 ? 's' : ''}
            </span>{' '}
            appeared in the latest scan.
          </p>
        </div>
      )}

      {!isLoading && clusters.length > 0 && (
        <div className="flex items-center gap-4 mb-4 text-sm text-text/60">
          <span>
            {clusters.length} cluster{clusters.length !== 1 ? 's' : ''}
          </span>
          {sharedClusters > 0 && (
            <span className="text-warning">{sharedClusters} multi-competitor</span>
          )}
        </div>
      )}

      {isLoading && <p className="text-text/50 text-sm">Loading relationships...</p>}

      {!isLoading && clusters.length === 0 && (
        <div className="text-center py-16 text-text/50">
          <p className="text-lg font-medium mb-2">No relationship data yet</p>
          <p className="text-sm">Click "Re-scan All" to scrape BuiltWith for connected domains.</p>
        </div>
      )}

      {!isLoading && clusters.length > 0 && (
        <div className="space-y-3">
          {clusters.map(c => (
            <ClusterCard key={c.competitors.join('|')} cluster={c} />
          ))}
        </div>
      )}
    </div>
  )
}
