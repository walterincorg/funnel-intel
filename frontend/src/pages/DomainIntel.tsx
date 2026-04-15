import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, type DomainFingerprint, type OperatorCluster, type DiscoveredDomain, type DomainChange } from '@/api/client'
import { cn } from '@/lib/utils'

type Tab = 'matrix' | 'clusters' | 'domains' | 'changes'

// Color palette for shared fingerprint values
const SHARED_COLORS = [
  'bg-blue-100 text-blue-800',
  'bg-pink-100 text-pink-800',
  'bg-green-100 text-green-800',
  'bg-purple-100 text-purple-800',
  'bg-orange-100 text-orange-800',
  'bg-cyan-100 text-cyan-800',
]

export function DomainIntel() {
  const [tab, setTab] = useState<Tab>('matrix')
  const queryClient = useQueryClient()

  const { data: stats } = useQuery({
    queryKey: ['domain-stats'],
    queryFn: api.domainStats,
  })

  const { data: runs } = useQuery({
    queryKey: ['domain-runs'],
    queryFn: api.domainRuns,
    refetchInterval: 3000,
  })

  const isScanning = runs?.[0]?.status === 'running' || runs?.[0]?.status === 'pending'

  const scanMutation = useMutation({
    mutationFn: api.triggerDomainScan,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domain'] }),
  })

  const tabs: { key: Tab; label: string }[] = [
    { key: 'matrix', label: 'Fingerprint Matrix' },
    { key: 'clusters', label: 'Operator Clusters' },
    { key: 'domains', label: 'New Domains' },
    { key: 'changes', label: 'Change Log' },
  ]

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
      <p className="text-sm text-text/60 mb-6">Infrastructure fingerprints and new domain discovery</p>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <StatCard label="Competitors tracked" value={stats.competitors_tracked} />
          <StatCard label="Operator clusters" value={stats.clusters_found} />
          <StatCard label="New domains (7d)" value={stats.new_domains_7d} />
          <StatCard label="Shared tracking codes" value={stats.shared_codes} />
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-0 border-b border-border mb-6">
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              'px-4 py-2.5 text-sm border-b-2 -mb-px transition-colors',
              tab === t.key
                ? 'border-accent text-text-bright font-medium'
                : 'border-transparent text-text/60 hover:text-text'
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'matrix' && <FingerprintMatrix />}
      {tab === 'clusters' && <ClusterView />}
      {tab === 'domains' && <DiscoveredDomainsView />}
      {tab === 'changes' && <ChangeLog />}
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-bg-card border border-border rounded-lg p-4">
      <div className="text-2xl font-bold text-text-bright">{value}</div>
      <div className="text-xs text-text/60 mt-1">{label}</div>
    </div>
  )
}

function FingerprintMatrix() {
  const { data: fingerprints, isLoading } = useQuery({
    queryKey: ['domain-fingerprints'],
    queryFn: () => api.listFingerprints(),
  })
  const { data: competitors } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  if (isLoading) return <div className="text-text/60 text-sm">Loading fingerprints...</div>
  if (!fingerprints?.length) {
    return (
      <div className="text-center py-12 text-text/60">
        <p className="text-lg font-medium mb-2">No fingerprints yet</p>
        <p className="text-sm">Click "Re-scan All" to extract tracking codes from competitor websites.</p>
      </div>
    )
  }

  // Build value -> count map for shared detection
  const valueCounts: Record<string, number> = {}
  for (const fp of fingerprints) {
    valueCounts[fp.fingerprint_value] = (valueCounts[fp.fingerprint_value] || 0) + 1
  }

  // Assign colors to shared values
  const sharedValues = Object.entries(valueCounts)
    .filter(([, count]) => count >= 2)
    .map(([val]) => val)
  const colorMap: Record<string, string> = {}
  sharedValues.forEach((val, i) => {
    colorMap[val] = SHARED_COLORS[i % SHARED_COLORS.length]
  })

  // Group fingerprints by competitor
  const compMap = new Map<string, { name: string; fingerprints: Record<string, DomainFingerprint[]> }>()
  const compNames: Record<string, string> = {}
  for (const c of competitors || []) {
    compNames[c.id] = c.name
  }

  for (const fp of fingerprints) {
    if (!compMap.has(fp.competitor_id)) {
      compMap.set(fp.competitor_id, {
        name: compNames[fp.competitor_id] || fp.competitor_id,
        fingerprints: {},
      })
    }
    const entry = compMap.get(fp.competitor_id)!
    if (!entry.fingerprints[fp.fingerprint_type]) {
      entry.fingerprints[fp.fingerprint_type] = []
    }
    entry.fingerprints[fp.fingerprint_type].push(fp)
  }

  const types = ['google_analytics', 'facebook_pixel', 'gtm', 'hosting', 'tech_stack']
  const typeLabels: Record<string, string> = {
    google_analytics: 'Google Analytics',
    facebook_pixel: 'Facebook Pixel',
    gtm: 'GTM',
    hosting: 'Hosting',
    tech_stack: 'Tech Stack',
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className="text-left py-3 px-3 font-semibold text-text-bright">Competitor</th>
            {types.map(t => (
              <th key={t} className="text-left py-3 px-3 font-semibold text-text-bright">{typeLabels[t]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...compMap.entries()].sort((a, b) => a[1].name.localeCompare(b[1].name)).map(([compId, data]) => (
            <tr key={compId} className="border-b border-border/50 hover:bg-bg-hover/50">
              <td className="py-2.5 px-3 font-medium text-text-bright">{data.name}</td>
              {types.map(type => {
                const fps = data.fingerprints[type] || []
                return (
                  <td key={type} className="py-2.5 px-3">
                    {fps.length === 0 ? (
                      <span className="text-text/30 text-xs italic">not detected</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {fps.map(fp => {
                          const color = colorMap[fp.fingerprint_value]
                          return (
                            <span
                              key={fp.id}
                              className={cn(
                                'px-2 py-0.5 rounded text-xs font-mono',
                                color || 'text-text/60'
                              )}
                              title={fp.raw_snippet || undefined}
                            >
                              {fp.fingerprint_value}
                            </span>
                          )
                        })}
                      </div>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ClusterView() {
  const { data: clusters, isLoading } = useQuery({
    queryKey: ['domain-clusters'],
    queryFn: () => api.listClusters('medium'),
  })

  if (isLoading) return <div className="text-text/60 text-sm">Loading clusters...</div>
  if (!clusters?.length) {
    return (
      <div className="text-center py-12 text-text/60">
        <p className="text-lg font-medium mb-2">No operator clusters found</p>
        <p className="text-sm">Clusters appear when two or more competitors share the same tracking code.</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {clusters.map(cluster => (
        <div key={cluster.id} className="bg-bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-3 mb-3">
            <span className={cn(
              'px-2 py-0.5 rounded text-xs font-medium uppercase',
              cluster.confidence === 'high' ? 'bg-red-100 text-red-800' :
              cluster.confidence === 'medium' ? 'bg-yellow-100 text-yellow-800' :
              'bg-gray-100 text-gray-600'
            )}>
              {cluster.confidence}
            </span>
            <span className="text-sm font-mono text-text/80">{cluster.fingerprint_type}: {cluster.fingerprint_value}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {cluster.members.map(m => (
              <span key={m.id} className="px-3 py-1 bg-bg-hover rounded-full text-sm text-text-bright">
                {m.name}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function DiscoveredDomainsView() {
  const [showLow, setShowLow] = useState(false)

  const { data: domains, isLoading } = useQuery({
    queryKey: ['discovered-domains', showLow],
    queryFn: () => api.listDiscoveredDomains({ min_relevance: showLow ? 'low' : 'medium' }),
  })

  if (isLoading) return <div className="text-text/60 text-sm">Loading domains...</div>
  if (!domains?.length) {
    return (
      <div className="text-center py-12 text-text/60">
        <p className="text-lg font-medium mb-2">No new domains discovered</p>
        <p className="text-sm">Domains appear from reverse tracking code lookups and keyword monitoring.</p>
      </div>
    )
  }

  const relevanceColors: Record<string, string> = {
    high: 'bg-green-100 text-green-800',
    medium: 'bg-yellow-100 text-yellow-800',
    low: 'bg-gray-100 text-gray-600',
  }
  const sourceColors: Record<string, string> = {
    reverse_lookup: 'bg-purple-100 text-purple-800',
    whois_monitor: 'bg-blue-100 text-blue-800',
    keyword_match: 'bg-orange-100 text-orange-800',
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <label className="flex items-center gap-2 text-sm text-text/60">
          <input
            type="checkbox"
            checked={showLow}
            onChange={e => setShowLow(e.target.checked)}
            className="rounded"
          />
          Show low-relevance (staging, parked domains)
        </label>
      </div>
      <div className="space-y-3">
        {domains.map(d => (
          <div key={d.id} className="bg-bg-card border border-border rounded-lg p-4">
            <div className="flex items-start justify-between">
              <div>
                <a
                  href={`https://${d.domain}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-semibold text-text-bright hover:text-accent"
                >
                  {d.domain}
                </a>
                {d.discovery_reason && (
                  <p className="text-xs text-text/60 mt-1">{d.discovery_reason}</p>
                )}
              </div>
              <span className="text-xs text-text/40">
                {d.first_seen_at ? new Date(d.first_seen_at).toLocaleDateString() : ''}
              </span>
            </div>
            <div className="flex gap-2 mt-2">
              <span className={cn('px-2 py-0.5 rounded text-xs', sourceColors[d.discovery_source] || 'bg-gray-100 text-gray-600')}>
                {d.discovery_source.replace('_', ' ')}
              </span>
              <span className={cn('px-2 py-0.5 rounded text-xs', relevanceColors[d.relevance] || '')}>
                {d.relevance}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function ChangeLog() {
  const { data: changes, isLoading } = useQuery({
    queryKey: ['domain-changes'],
    queryFn: () => api.listDomainChanges({ days: 30 }),
  })
  const { data: competitors } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  if (isLoading) return <div className="text-text/60 text-sm">Loading changes...</div>
  if (!changes?.length) {
    return (
      <div className="text-center py-12 text-text/60">
        <p className="text-lg font-medium mb-2">No changes detected</p>
        <p className="text-sm">Changes appear when competitors add, remove, or change tracking codes.</p>
      </div>
    )
  }

  const compNames: Record<string, string> = {}
  for (const c of competitors || []) {
    compNames[c.id] = c.name
  }

  const changeTypeLabels: Record<string, string> = {
    code_added: 'Added',
    code_removed: 'Removed',
    hosting_changed: 'Hosting changed',
    tech_changed: 'Tech stack changed',
  }

  return (
    <div className="space-y-3">
      {changes.map(ch => (
        <div key={ch.id} className="bg-bg-card border border-border rounded-lg p-4 flex items-start gap-4">
          <span className="text-xs text-text/40 min-w-[80px] pt-0.5">
            {ch.detected_at ? new Date(ch.detected_at).toLocaleDateString() : ''}
          </span>
          <div>
            <p className="text-sm text-text-bright">
              <span className="font-medium">{compNames[ch.competitor_id] || ch.competitor_id}</span>
              {' '}{changeTypeLabels[ch.change_type] || ch.change_type}{' '}
              <span className="font-mono text-xs">{ch.fingerprint_type}</span>
            </p>
            <p className="text-xs text-text/60 mt-1">
              {ch.old_value && <span className="line-through mr-2">{ch.old_value}</span>}
              {ch.new_value && <span className="text-green-600">{ch.new_value}</span>}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}
