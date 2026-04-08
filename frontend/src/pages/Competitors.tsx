import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Plus, Trash2, ExternalLink, X } from 'lucide-react'
import { api } from '@/api/client'

function AddCompetitorModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [url, setUrl] = useState('')

  const mutation = useMutation({
    mutationFn: () => api.createCompetitor({ name, slug, funnel_url: url }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['competitors'] })
      onClose()
    },
  })

  const inputClass = 'w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-bright placeholder:text-text/30 focus:outline-none focus:border-accent'

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-bg-card rounded-xl border border-border p-6 w-full max-w-md" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-medium text-text-bright">Add Competitor</h2>
          <button onClick={onClose} className="p-1 hover:bg-bg-hover rounded">
            <X size={18} className="text-text/50" />
          </button>
        </div>
        <form onSubmit={e => { e.preventDefault(); mutation.mutate() }} className="space-y-3">
          <div>
            <label className="text-xs text-text/60 block mb-1">Name</label>
            <input value={name} onChange={e => { setName(e.target.value); if (!slug) setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, '-')) }} placeholder="MadMuscles" className={inputClass} required />
          </div>
          <div>
            <label className="text-xs text-text/60 block mb-1">Slug</label>
            <input value={slug} onChange={e => setSlug(e.target.value)} placeholder="madmuscles" className={inputClass} required />
          </div>
          <div>
            <label className="text-xs text-text/60 block mb-1">Funnel URL</label>
            <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://madmuscles.com/quiz" className={inputClass} required type="url" />
          </div>
          <button
            type="submit"
            disabled={mutation.isPending}
            className="w-full py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/80 transition-colors disabled:opacity-50"
          >
            {mutation.isPending ? 'Adding...' : 'Add Competitor'}
          </button>
          {mutation.isError && (
            <p className="text-xs text-danger">{(mutation.error as Error).message}</p>
          )}
        </form>
      </div>
    </div>
  )
}

export function Competitors() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)

  const { data: competitors, isLoading } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteCompetitor(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['competitors'] }),
  })

  if (isLoading) {
    return <div className="text-text/50 py-12 text-center">Loading...</div>
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-text-bright">Competitors</h1>
          <p className="text-sm text-text/60 mt-1">Manage your tracked competitors</p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/80 transition-colors"
        >
          <Plus size={16} /> Add
        </button>
      </div>

      {competitors && competitors.length > 0 ? (
        <div className="space-y-2">
          {competitors.map(comp => (
            <div
              key={comp.id}
              className="bg-bg-card rounded-lg border border-border p-4 hover:border-accent/30 transition-colors flex items-center justify-between"
            >
              <div
                className="cursor-pointer flex-1 min-w-0"
                onClick={() => navigate(`/competitors/${comp.id}`)}
              >
                <h3 className="text-text-bright font-medium">{comp.name}</h3>
                <a
                  href={comp.funnel_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-accent/70 hover:text-accent flex items-center gap-1 mt-0.5"
                  onClick={e => e.stopPropagation()}
                >
                  {new URL(comp.funnel_url).hostname + new URL(comp.funnel_url).pathname} <ExternalLink size={10} />
                </a>
              </div>
              <button
                onClick={() => { if (confirm(`Delete ${comp.name}?`)) deleteMutation.mutate(comp.id) }}
                className="p-2 rounded hover:bg-danger/10 text-text/30 hover:text-danger transition-colors ml-3"
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center">
          <p className="text-text/50">No competitors yet.</p>
        </div>
      )}

      {showAdd && <AddCompetitorModal onClose={() => setShowAdd(false)} />}
    </div>
  )
}
