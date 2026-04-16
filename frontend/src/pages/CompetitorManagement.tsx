import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, ChevronDown, ChevronRight, Save, X } from 'lucide-react'
import { api, type Competitor } from '@/api/client'

const inputClass =
  'w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-bright placeholder:text-text/30 focus:outline-none focus:border-accent'
const labelClass = 'text-xs text-text/60 block mb-1'

interface CompetitorForm {
  name: string
  slug: string
  funnel_url: string
  brand_keyword: string
  ads_library_url: string
}

const emptyForm: CompetitorForm = {
  name: '',
  slug: '',
  funnel_url: '',
  brand_keyword: '',
  ads_library_url: '',
}

function formFromCompetitor(c: Competitor): CompetitorForm {
  return {
    name: c.name,
    slug: c.slug,
    funnel_url: c.funnel_url,
    brand_keyword: c.brand_keyword ?? '',
    ads_library_url: c.ads_library_url ?? '',
  }
}

function CompetitorRow({ competitor }: { competitor: Competitor }) {
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const [form, setForm] = useState<CompetitorForm>(() => formFromCompetitor(competitor))
  const [dirty, setDirty] = useState(false)

  const updateField = (field: keyof CompetitorForm, value: string) => {
    setForm(prev => ({ ...prev, [field]: value }))
    setDirty(true)
  }

  const updateMutation = useMutation({
    mutationFn: () => {
      const payload: Record<string, string> = {}
      if (form.name !== competitor.name) payload.name = form.name
      if (form.slug !== competitor.slug) payload.slug = form.slug
      if (form.funnel_url !== competitor.funnel_url) payload.funnel_url = form.funnel_url
      if (form.brand_keyword !== (competitor.brand_keyword ?? ''))
        payload.brand_keyword = form.brand_keyword || ''
      if (form.ads_library_url !== (competitor.ads_library_url ?? ''))
        payload.ads_library_url = form.ads_library_url || ''
      return api.updateCompetitor(competitor.id, payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['competitors'] })
      setDirty(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteCompetitor(competitor.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['competitors'] }),
  })

  return (
    <div className="bg-bg-card rounded-lg border border-border overflow-hidden">
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-bg-hover transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3 min-w-0">
          {expanded ? (
            <ChevronDown size={16} className="text-text/40 shrink-0" />
          ) : (
            <ChevronRight size={16} className="text-text/40 shrink-0" />
          )}
          <div className="min-w-0">
            <h3 className="text-text-bright font-medium truncate">{competitor.name}</h3>
            <p className="text-xs text-text/40 truncate">{competitor.slug}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-text/40 shrink-0">
          {competitor.funnel_url && (
            <span className="hidden sm:inline truncate max-w-48">
              {new URL(competitor.funnel_url).hostname}
            </span>
          )}
        </div>
      </div>

      {expanded && (
        <div className="border-t border-border px-4 py-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Name</label>
              <input
                value={form.name}
                onChange={e => updateField('name', e.target.value)}
                className={inputClass}
                required
              />
            </div>
            <div>
              <label className={labelClass}>Slug</label>
              <input
                value={form.slug}
                onChange={e => updateField('slug', e.target.value)}
                className={inputClass}
                required
              />
            </div>
          </div>
          <div>
            <label className={labelClass}>Funnel URL</label>
            <input
              value={form.funnel_url}
              onChange={e => updateField('funnel_url', e.target.value)}
              className={inputClass}
              placeholder="https://example.com/quiz"
              type="url"
              required
            />
          </div>
          <div>
            <label className={labelClass}>Brand Keyword</label>
            <input
              value={form.brand_keyword}
              onChange={e => updateField('brand_keyword', e.target.value)}
              className={inputClass}
              placeholder="e.g. madmuscles (for WHOIS monitoring)"
            />
          </div>
          <div>
            <label className={labelClass}>Ads Library URL</label>
            <input
              value={form.ads_library_url}
              onChange={e => updateField('ads_library_url', e.target.value)}
              className={inputClass}
              placeholder="https://www.facebook.com/ads/library/..."
              type="url"
            />
          </div>

          <div className="flex items-center justify-between pt-2">
            <button
              onClick={e => {
                e.stopPropagation()
                if (confirm(`Delete ${competitor.name}? This removes all related scans, ads, and data.`))
                  deleteMutation.mutate()
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-danger/70 hover:bg-danger/10 hover:text-danger transition-colors"
            >
              <Trash2 size={14} /> Delete
            </button>
            <div className="flex items-center gap-2">
              {dirty && (
                <button
                  onClick={() => {
                    setForm(formFromCompetitor(competitor))
                    setDirty(false)
                  }}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-text/50 hover:bg-bg-hover transition-colors"
                >
                  <X size={14} /> Discard
                </button>
              )}
              <button
                onClick={() => updateMutation.mutate()}
                disabled={!dirty || updateMutation.isPending}
                className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs bg-accent text-white font-medium hover:bg-accent/80 transition-colors disabled:opacity-40"
              >
                <Save size={14} /> {updateMutation.isPending ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
          {updateMutation.isError && (
            <p className="text-xs text-danger">{(updateMutation.error as Error).message}</p>
          )}
          {updateMutation.isSuccess && !dirty && (
            <p className="text-xs text-green-400">Saved successfully.</p>
          )}
        </div>
      )}
    </div>
  )
}

function AddCompetitorForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<CompetitorForm>({ ...emptyForm })

  const updateField = (field: keyof CompetitorForm, value: string) => {
    setForm(prev => ({ ...prev, [field]: value }))
  }

  const mutation = useMutation({
    mutationFn: () =>
      api.createCompetitor({
        name: form.name,
        slug: form.slug,
        funnel_url: form.funnel_url,
        brand_keyword: form.brand_keyword || undefined,
        ads_library_url: form.ads_library_url || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['competitors'] })
      onClose()
    },
  })

  return (
    <div className="bg-bg-card rounded-lg border border-accent/30 overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <h3 className="text-text-bright font-medium">New Competitor</h3>
        <button onClick={onClose} className="p-1 hover:bg-bg-hover rounded">
          <X size={16} className="text-text/50" />
        </button>
      </div>
      <form
        onSubmit={e => {
          e.preventDefault()
          mutation.mutate()
        }}
        className="px-4 py-4 space-y-3"
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className={labelClass}>Name *</label>
            <input
              value={form.name}
              onChange={e => {
                updateField('name', e.target.value)
                if (!form.slug)
                  updateField('slug', e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, '-'))
              }}
              className={inputClass}
              placeholder="MadMuscles"
              required
            />
          </div>
          <div>
            <label className={labelClass}>Slug *</label>
            <input
              value={form.slug}
              onChange={e => updateField('slug', e.target.value)}
              className={inputClass}
              placeholder="madmuscles"
              required
            />
          </div>
        </div>
        <div>
          <label className={labelClass}>Funnel URL *</label>
          <input
            value={form.funnel_url}
            onChange={e => updateField('funnel_url', e.target.value)}
            className={inputClass}
            placeholder="https://madmuscles.com/quiz"
            type="url"
            required
          />
        </div>
        <div>
          <label className={labelClass}>Brand Keyword</label>
          <input
            value={form.brand_keyword}
            onChange={e => updateField('brand_keyword', e.target.value)}
            className={inputClass}
            placeholder="e.g. madmuscles (for WHOIS monitoring)"
          />
        </div>
        <div>
          <label className={labelClass}>Ads Library URL</label>
          <input
            value={form.ads_library_url}
            onChange={e => updateField('ads_library_url', e.target.value)}
            className={inputClass}
            placeholder="https://www.facebook.com/ads/library/..."
            type="url"
          />
        </div>
        <div className="flex justify-end pt-1">
          <button
            type="submit"
            disabled={mutation.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/80 transition-colors disabled:opacity-50"
          >
            <Plus size={16} /> {mutation.isPending ? 'Adding...' : 'Add Competitor'}
          </button>
        </div>
        {mutation.isError && (
          <p className="text-xs text-danger">{(mutation.error as Error).message}</p>
        )}
      </form>
    </div>
  )
}

export function CompetitorManagement() {
  const [showAdd, setShowAdd] = useState(false)

  const { data: competitors, isLoading } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  if (isLoading) {
    return <div className="text-text/50 py-12 text-center">Loading...</div>
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-text-bright">Manage Competitors</h1>
          <p className="text-sm text-text/60 mt-1">
            Add, edit, and configure tracked competitors
          </p>
        </div>
        {!showAdd && (
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/80 transition-colors"
          >
            <Plus size={16} /> Add
          </button>
        )}
      </div>

      <div className="space-y-2">
        {showAdd && (
          <AddCompetitorForm onClose={() => setShowAdd(false)} />
        )}

        {competitors && competitors.length > 0 ? (
          competitors.map(comp => <CompetitorRow key={comp.id} competitor={comp} />)
        ) : (
          !showAdd && (
            <div className="bg-bg-card rounded-xl border border-border p-8 text-center">
              <p className="text-text/50">No competitors yet. Click Add to create one.</p>
            </div>
          )
        )}
      </div>
    </div>
  )
}
