import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Sparkles, ExternalLink, Users } from 'lucide-react'
import { api, type BuiltWithRelationship } from '@/api/client'
import { cn, checkActive, getPrevRunCutoff } from '@/lib/utils'

function isNew(r: BuiltWithRelationship, prevRunAt: string | null): boolean {
  return !!r.first_seen_at && !!prevRunAt && new Date(r.first_seen_at) > new Date(prevRunAt)
}

interface RelatedGroup {
  relatedDomain: string
  sources: Set<string>           // competitor source_domains connecting to this related_domain
  attributes: Set<string>        // distinct shared attribute values
  rows: BuiltWithRelationship[]  // underlying rows, for expand details
  firstDetectedMin: string | null
  lastDetectedMax: string | null
  hasNew: boolean
  allInactive: boolean
}

function buildGroups(rows: BuiltWithRelationship[], prevRunAt: string | null): RelatedGroup[] {
  const map = new Map<string, RelatedGroup>()

  for (const r of rows) {
    let g = map.get(r.related_domain)
    if (!g) {
      g = {
        relatedDomain: r.related_domain,
        sources: new Set(),
        attributes: new Set(),
        rows: [],
        firstDetectedMin: null,
        lastDetectedMax: null,
        hasNew: false,
        allInactive: true,
      }
      map.set(r.related_domain, g)
    }
    g.sources.add(r.source_domain)
    if (r.attribute_value) g.attributes.add(r.attribute_value)
    g.rows.push(r)
    if (r.first_detected) {
      if (!g.firstDetectedMin || r.first_detected < g.firstDetectedMin) g.firstDetectedMin = r.first_detected
    }
    if (r.last_detected) {
      if (!g.lastDetectedMax || r.last_detected > g.lastDetectedMax) g.lastDetectedMax = r.last_detected
    }
    if (isNew(r, prevRunAt)) g.hasNew = true
    if (checkActive(r.last_detected)) g.allInactive = false
  }

  // Sort: most-shared first, then active, then alphabetical
  return [...map.values()].sort((a, b) => {
    if (a.sources.size !== b.sources.size) return b.sources.size - a.sources.size
    if (a.allInactive !== b.allInactive) return a.allInactive ? 1 : -1
    return a.relatedDomain.localeCompare(b.relatedDomain)
  })
}

function RelatedGroupCard({ group, prevRunAt }: { group: RelatedGroup; prevRunAt: string | null }) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  const newRowCount = group.rows.filter(r => isNew(r, prevRunAt)).length

  return (
    <div
      className={cn(
        'bg-bg-card border rounded-xl p-5',
        group.allInactive ? 'border-border/40 opacity-60' : 'border-border',
      )}
    >
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <a
              href={`https://${group.relatedDomain}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 font-mono text-accent hover:underline font-semibold"
            >
              {group.relatedDomain}
              <ExternalLink size={11} />
            </a>
            {group.sources.size >= 2 && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-warning/10 text-warning">
                <Users size={10} />
                {group.sources.size} competitors
              </span>
            )}
            {group.hasNew && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-info/10 text-info">
                <Sparkles size={10} />
                {newRowCount} new
              </span>
            )}
            {group.allInactive && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-text/5 text-text/40">inactive</span>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {[...group.sources].sort().map(src => (
              <span
                key={src}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-mono bg-bg-hover/60 text-text/70 border border-border/40"
              >
                {src}
              </span>
            ))}
          </div>
        </div>
        <div className="text-right text-xs text-text/50 shrink-0">
          <div>{group.attributes.size} shared attr{group.attributes.size !== 1 ? 's' : ''}</div>
          <div>{group.rows.length} row{group.rows.length !== 1 ? 's' : ''}</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5 mb-3">
        {[...group.attributes].slice(0, 6).map(attr => (
          <span
            key={attr}
            className="px-2 py-0.5 rounded text-[11px] font-mono bg-accent-dim/40 text-accent border border-accent/20"
          >
            {attr}
          </span>
        ))}
        {group.attributes.size > 6 && (
          <span className="px-2 py-0.5 rounded text-[11px] text-text/40">
            +{group.attributes.size - 6} more
          </span>
        )}
      </div>

      <div className="flex items-center justify-between text-xs text-text/50">
        <div>
          {group.firstDetectedMin && <span>First: {group.firstDetectedMin}</span>}
          {group.firstDetectedMin && group.lastDetectedMax && <span className="mx-2">·</span>}
          {group.lastDetectedMax && <span>Last: {group.lastDetectedMax}</span>}
        </div>
        <button
          onClick={() => setDetailsOpen(o => !o)}
          className="flex items-center gap-1 text-text/50 hover:text-text transition-colors"
        >
          <ChevronDown size={12} className={cn('transition-transform', detailsOpen && 'rotate-180')} />
          Details
        </button>
      </div>

      {detailsOpen && (
        <div className="mt-3 pt-3 border-t border-border/40 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text/50 uppercase tracking-wide">
                <th className="text-left py-1 pr-4">Competitor</th>
                <th className="text-left py-1 pr-4">Shared attribute</th>
                <th className="text-left py-1 pr-4">First</th>
                <th className="text-left py-1 pr-4">Last</th>
                <th className="text-left py-1">Overlap</th>
              </tr>
            </thead>
            <tbody>
              {group.rows.map(r => (
                <tr key={r.id} className="border-t border-border/20">
                  <td className="py-1.5 pr-4 font-mono text-text/70">{r.source_domain}</td>
                  <td className="py-1.5 pr-4">{r.attribute_value ?? '—'}</td>
                  <td className="py-1.5 pr-4 text-text/50">{r.first_detected ?? '—'}</td>
                  <td className="py-1.5 pr-4 text-text/50">{r.last_detected ?? '—'}</td>
                  <td className="py-1.5 text-text/50">{r.overlap_duration ?? '—'}</td>
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

  const allGroups = useMemo(() => buildGroups(relationships ?? [], prevRunAt), [relationships, prevRunAt])
  const visibleGroups = activeOnly ? allGroups.filter(g => !g.allInactive) : allGroups

  const totalNew = useMemo(
    () => (relationships ?? []).filter(r => checkActive(r.last_detected) && isNew(r, prevRunAt)).length,
    [relationships, prevRunAt],
  )

  const sharedCount = visibleGroups.filter(g => g.sources.size >= 2).length

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
        BuiltWith relationships grouped by related domain — the same domain connected to multiple
        competitors is a strong signal they share an operator or network.
      </p>

      {totalNew > 0 && (
        <div className="flex items-center gap-2 p-3 mb-6 rounded-lg bg-info/5 border border-info/20">
          <Sparkles size={15} className="text-info shrink-0" />
          <p className="text-sm text-info">
            <span className="font-semibold">{totalNew} new row{totalNew !== 1 ? 's' : ''}</span>
            {' '}appeared in the latest scan.
          </p>
        </div>
      )}

      {!isLoading && allGroups.length > 0 && (
        <div className="flex items-center justify-between mb-4 text-sm text-text/60">
          <div className="flex items-center gap-4">
            <span>{visibleGroups.length} related domain{visibleGroups.length !== 1 ? 's' : ''}</span>
            {sharedCount > 0 && (
              <span className="text-warning">
                {sharedCount} shared across multiple competitors
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

      {!isLoading && allGroups.length === 0 && (
        <div className="text-center py-16 text-text/50">
          <p className="text-lg font-medium mb-2">No relationship data yet</p>
          <p className="text-sm">Click "Re-scan All" to scrape BuiltWith for connected domains.</p>
        </div>
      )}

      {!isLoading && visibleGroups.length > 0 && (
        <div className="space-y-3">
          {visibleGroups.map(g => (
            <RelatedGroupCard key={g.relatedDomain} group={g} prevRunAt={prevRunAt} />
          ))}
        </div>
      )}
    </div>
  )
}
