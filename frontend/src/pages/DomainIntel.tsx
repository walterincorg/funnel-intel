import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, type DomainFingerprint } from '@/api/client'
import { cn } from '@/lib/utils'

type Tab = 'matrix' | 'clusters' | 'domains'

const SHARED_COLORS = [
  'bg-blue-100 text-blue-800',
  'bg-pink-100 text-pink-800',
  'bg-green-100 text-green-800',
  'bg-purple-100 text-purple-800',
  'bg-orange-100 text-orange-800',
  'bg-cyan-100 text-cyan-800',
]

const TYPE_LABELS: Record<string, string> = {
  google_analytics: 'Google Analytics',
  facebook_pixel: 'Facebook Pixel',
  gtm: 'Google Tag Manager',
}
const TYPES = ['google_analytics', 'facebook_pixel', 'gtm'] as const

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
      <p className="text-sm text-text/60 mb-6">GA/Pixel fingerprints and brand-prefixed WHOIS monitoring</p>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <StatCard label="Competitors tracked" value={stats.competitors_tracked} />
          <StatCard label="Operator clusters" value={stats.clusters_found} />
          <StatCard label="New domains (7d)" value={stats.new_domains_7d} />
          <StatCard label="Shared tracking codes" value={stats.shared_codes} />
        </div>
      )}

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
        <p className="text-sm">Click "Re-scan All" to extract GA and Pixel IDs from competitor websites.</p>
      </div>
    )
  }

  const valueCounts: Record<string, number> = {}
  for (const fp of fingerprints) {
    valueCounts[fp.fingerprint_value] = (valueCounts[fp.fingerprint_value] || 0) + 1
  }

  const sharedValues = Object.entries(valueCounts)
    .filter(([, count]) => count >= 2)
    .map(([val]) => val)
  const colorMap: Record<string, string> = {}
  sharedValues.forEach((val, i) => {
    colorMap[val] = SHARED_COLORS[i % SHARED_COLORS.length]
  })

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

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className="text-left py-3 px-3 font-semibold text-text-bright">Competitor</th>
            {TYPES.map(t => (
              <th key={t} className="text-left py-3 px-3 font-semibold text-text-bright">{TYPE_LABELS[t]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...compMap.entries()].sort((a, b) => a[1].name.localeCompare(b[1].name)).map(([compId, data]) => (
            <tr key={compId} className="border-b border-border/50 hover:bg-bg-hover/50">
              <td className="py-2.5 px-3 font-medium text-text-bright">{data.name}</td>
              {TYPES.map(type => {
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
    queryFn: api.listClusters,
  })

  if (isLoading) return <div className="text-text/60 text-sm">Loading clusters...</div>
  if (!clusters?.length) {
    return (
      <div className="text-center py-12 text-text/60">
        <p className="text-lg font-medium mb-2">No operator clusters found</p>
        <p className="text-sm">Clusters appear when two or more competitors share a GA or Pixel ID.</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {clusters.map(cluster => (
        <div key={cluster.id} className="bg-bg-card border border-border rounded-lg p-4">
          <div className="flex items-center gap-3 mb-3">
            <span className="px-2 py-0.5 rounded text-xs font-medium uppercase bg-red-100 text-red-800">
              {TYPE_LABELS[cluster.fingerprint_type] || cluster.fingerprint_type}
            </span>
            <span className="text-sm font-mono text-text/80">{cluster.fingerprint_value}</span>
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
  const { data: domains, isLoading } = useQuery({
    queryKey: ['discovered-domains'],
    queryFn: () => api.listDiscoveredDomains(),
  })

  if (isLoading) return <div className="text-text/60 text-sm">Loading domains...</div>
  if (!domains?.length) {
    return (
      <div className="text-center py-12 text-text/60">
        <p className="text-lg font-medium mb-2">No new domains discovered</p>
        <p className="text-sm">Domains appear when WHOIS registers a new match for a tracked brand keyword.</p>
      </div>
    )
  }

  return (
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
        </div>
      ))}
    </div>
  )
}
