import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Sparkles, ExternalLink } from 'lucide-react'
import { api, type BuiltWithRelationship } from '@/api/client'
import { cn, checkActive, getPrevRunCutoff } from '@/lib/utils'

function isNew(r: BuiltWithRelationship, prevRunAt: string | null): boolean {
  return !!r.first_seen_at && !!prevRunAt && new Date(r.first_seen_at) > new Date(prevRunAt)
}

function CollapsibleInactive({ rows, prevRunAt }: { rows: BuiltWithRelationship[]; prevRunAt: string | null }) {
  const [open, setOpen] = useState(false)
  if (!rows.length) return null
  return (
    <div className="mt-3 border-t border-border/50 pt-2">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-xs text-text/40 hover:text-text/60 transition-colors"
      >
        <ChevronDown size={12} className={cn('transition-transform', open && 'rotate-180')} />
        {rows.length} inactive
      </button>
      {open && (
        <table className="w-full mt-3 text-xs text-text/30">
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="border-b border-border/20">
                <td className="py-1.5 pr-4 font-mono">{r.related_domain}</td>
                <td className="py-1.5 pr-4">{r.attribute_value ?? '—'}</td>
                <td className="py-1.5 pr-4">{r.first_detected ?? '—'}</td>
                <td className="py-1.5 pr-4">{r.last_detected ?? '—'}</td>
                <td className="py-1.5">{r.overlap_duration ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function CompetitorRelationshipCard({
  domain,
  rows,
  prevRunAt,
}: {
  domain: string
  rows: BuiltWithRelationship[]
  prevRunAt: string | null
}) {
  const active = rows.filter(r => checkActive(r.last_detected))
  const inactive = rows.filter(r => !checkActive(r.last_detected))
  const newCount = active.filter(r => isNew(r, prevRunAt)).length

  return (
    <div className="bg-bg-card border border-border rounded-xl p-5">
      <div className="flex items-center gap-3 mb-4">
        <h3 className="text-sm font-semibold text-text-bright font-mono">{domain}</h3>
        {newCount > 0 && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-info/10 text-info">
            <Sparkles size={10} />
            {newCount} new
          </span>
        )}
      </div>

      {active.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50">
                <th className="text-left py-2 pr-4 text-xs font-medium text-text/50 uppercase tracking-wide">Related domain</th>
                <th className="text-left py-2 pr-4 text-xs font-medium text-text/50 uppercase tracking-wide">Shared attribute</th>
                <th className="text-left py-2 pr-4 text-xs font-medium text-text/50 uppercase tracking-wide">First detected</th>
                <th className="text-left py-2 pr-4 text-xs font-medium text-text/50 uppercase tracking-wide">Last detected</th>
                <th className="text-left py-2 text-xs font-medium text-text/50 uppercase tracking-wide">Overlap</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {active.map(r => (
                <tr key={r.id} className="border-b border-border/30 hover:bg-bg-hover/40">
                  <td className="py-2 pr-4">
                    <a
                      href={`https://${r.related_domain}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 font-mono text-accent hover:underline"
                    >
                      {r.related_domain}
                      <ExternalLink size={10} />
                    </a>
                  </td>
                  <td className="py-2 pr-4 text-text/70">{r.attribute_value ?? '—'}</td>
                  <td className="py-2 pr-4 text-text/50">{r.first_detected ?? '—'}</td>
                  <td className="py-2 pr-4 text-text/50">{r.last_detected ?? '—'}</td>
                  <td className="py-2 text-text/50">{r.overlap_duration ?? '—'}</td>
                  <td className="py-2 text-right">
                    {isNew(r, prevRunAt) && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-xs font-medium bg-info/10 text-info">
                        <Sparkles size={9} />
                        New
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-text/40 italic">No active relationships.</p>
      )}

      <CollapsibleInactive rows={inactive} prevRunAt={prevRunAt} />
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

  const isScanning = runs?.[0]?.status === 'running' || runs?.[0]?.status === 'pending'

  const scanMutation = useMutation({
    mutationFn: api.triggerDomainScan,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domain'] }),
  })

  const prevRunAt = getPrevRunCutoff(runs ?? [])

  // Group by source_domain
  const grouped = new Map<string, BuiltWithRelationship[]>()
  for (const r of relationships ?? []) {
    if (!grouped.has(r.source_domain)) grouped.set(r.source_domain, [])
    grouped.get(r.source_domain)!.push(r)
  }

  const totalNew = [...(relationships ?? [])]
    .filter(r => checkActive(r.last_detected) && isNew(r, prevRunAt))
    .length

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
      <p className="text-sm text-text/60 mb-6">BuiltWith relationship graph — connected domains sharing technology attributes</p>

      {totalNew > 0 && (
        <div className="flex items-center gap-2 p-3 mb-6 rounded-lg bg-info/5 border border-info/20">
          <Sparkles size={15} className="text-info shrink-0" />
          <p className="text-sm text-info">
            <span className="font-semibold">{totalNew} new related domain{totalNew !== 1 ? 's' : ''}</span>
            {' '}appeared in the latest scan.
          </p>
        </div>
      )}

      {isLoading && (
        <p className="text-text/50 text-sm">Loading relationships...</p>
      )}

      {!isLoading && grouped.size === 0 && (
        <div className="text-center py-16 text-text/50">
          <p className="text-lg font-medium mb-2">No relationship data yet</p>
          <p className="text-sm">Click "Re-scan All" to scrape BuiltWith for connected domains.</p>
        </div>
      )}

      {!isLoading && grouped.size > 0 && (
        <div className="space-y-4">
          {[...grouped.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([domain, rows]) => (
            <CompetitorRelationshipCard key={domain} domain={domain} rows={rows} prevRunAt={prevRunAt} />
          ))}
        </div>
      )}
    </div>
  )
}
