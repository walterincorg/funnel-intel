import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Save, Users, Clock, Megaphone, Globe, ArrowRight } from 'lucide-react'
import { api, type AppSettings } from '@/api/client'

const inputClass =
  'w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-bright focus:outline-none focus:border-accent'
const labelClass = 'text-xs text-text/60 block mb-1'
const cardClass = 'bg-bg-card rounded-lg border border-border p-5 space-y-4'

const INTERVAL_OPTIONS = [
  { label: 'Every 90 minutes', value: 90 },
  { label: 'Every 6 hours', value: 360 },
  { label: 'Every 12 hours', value: 720 },
  { label: 'Every day', value: 1440 },
  { label: 'Every 2 days', value: 2880 },
  { label: 'Every 3 days', value: 4320 },
]

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const HOURS = Array.from({ length: 24 }, (_, i) => i)

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
}) {
  return (
    <label className="flex items-center gap-3 cursor-pointer">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative w-10 h-5 rounded-full transition-colors ${
          checked ? 'bg-accent' : 'bg-border'
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
            checked ? 'translate-x-5' : ''
          }`}
        />
      </button>
      <span className="text-sm text-text-bright">{label}</span>
    </label>
  )
}

export function Settings() {
  const queryClient = useQueryClient()
  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  })
  const { data: competitors } = useQuery({
    queryKey: ['competitors'],
    queryFn: api.listCompetitors,
  })

  const [form, setForm] = useState<Partial<AppSettings>>({})
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (settings) {
      setForm({
        funnel_scan_interval_minutes: settings.funnel_scan_interval_minutes,
        funnel_scan_enabled: settings.funnel_scan_enabled,
        ad_scrape_enabled: settings.ad_scrape_enabled,
        ad_scrape_hour_utc: settings.ad_scrape_hour_utc,
        ad_scrape_days_of_week: settings.ad_scrape_days_of_week,
        domain_intel_enabled: settings.domain_intel_enabled,
        domain_intel_day_of_week: settings.domain_intel_day_of_week,
        domain_intel_hour_utc: settings.domain_intel_hour_utc,
      })
      setDirty(false)
    }
  }, [settings])

  const update = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    setForm(prev => ({ ...prev, [key]: value }))
    setDirty(true)
  }

  const toggleDay = (day: number) => {
    const current = form.ad_scrape_days_of_week ?? []
    const next = current.includes(day)
      ? current.filter(d => d !== day)
      : [...current, day].sort()
    update('ad_scrape_days_of_week', next)
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!settings) return Promise.reject()
      const patch: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(form)) {
        const orig = settings[key as keyof AppSettings]
        if (JSON.stringify(value) !== JSON.stringify(orig)) {
          patch[key] = value
        }
      }
      if (Object.keys(patch).length === 0) return Promise.resolve(settings)
      return api.updateSettings(patch as Partial<AppSettings>)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      setDirty(false)
    },
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-text/40">
        Loading settings...
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text-bright">Settings</h1>
        <button
          onClick={() => saveMutation.mutate()}
          disabled={!dirty || saveMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-lg text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Save size={16} />
          {saveMutation.isPending ? 'Saving...' : 'Save Changes'}
        </button>
      </div>

      {saveMutation.isError && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-400">
          Failed to save settings. Please try again.
        </div>
      )}

      {/* Funnel Scans */}
      <div className={cardClass}>
        <div className="flex items-center gap-3 mb-1">
          <Clock size={18} className="text-accent" />
          <h2 className="text-base font-medium text-text-bright">Funnel Scans</h2>
        </div>

        <Toggle
          checked={form.funnel_scan_enabled ?? true}
          onChange={v => update('funnel_scan_enabled', v)}
          label="Auto-schedule enabled"
        />

        <div>
          <label className={labelClass}>Scan Interval</label>
          <select
            value={form.funnel_scan_interval_minutes ?? 90}
            onChange={e => update('funnel_scan_interval_minutes', Number(e.target.value))}
            className={inputClass}
            disabled={!form.funnel_scan_enabled}
          >
            {INTERVAL_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Ad Scrapes */}
      <div className={cardClass}>
        <div className="flex items-center gap-3 mb-1">
          <Megaphone size={18} className="text-accent" />
          <h2 className="text-base font-medium text-text-bright">Ad Scrapes</h2>
        </div>

        <Toggle
          checked={form.ad_scrape_enabled ?? false}
          onChange={v => update('ad_scrape_enabled', v)}
          label="Auto-schedule enabled"
        />

        <div>
          <label className={labelClass}>Run at hour (UTC)</label>
          <select
            value={form.ad_scrape_hour_utc ?? 6}
            onChange={e => update('ad_scrape_hour_utc', Number(e.target.value))}
            className={inputClass}
            disabled={!form.ad_scrape_enabled}
          >
            {HOURS.map(h => (
              <option key={h} value={h}>
                {String(h).padStart(2, '0')}:00 UTC
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className={labelClass}>Days of week</label>
          <div className="flex gap-2 flex-wrap">
            {DAY_NAMES.map((name, i) => (
              <button
                key={i}
                type="button"
                disabled={!form.ad_scrape_enabled}
                onClick={() => toggleDay(i)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  (form.ad_scrape_days_of_week ?? []).includes(i)
                    ? 'bg-accent text-white'
                    : 'bg-bg border border-border text-text/60 hover:border-accent/50'
                } disabled:opacity-40 disabled:cursor-not-allowed`}
              >
                {name}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Domain Intel */}
      <div className={cardClass}>
        <div className="flex items-center gap-3 mb-1">
          <Globe size={18} className="text-accent" />
          <h2 className="text-base font-medium text-text-bright">Domain Intel</h2>
        </div>

        <Toggle
          checked={form.domain_intel_enabled ?? true}
          onChange={v => update('domain_intel_enabled', v)}
          label="Auto-schedule enabled"
        />

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className={labelClass}>Day of week</label>
            <select
              value={form.domain_intel_day_of_week ?? 1}
              onChange={e => update('domain_intel_day_of_week', Number(e.target.value))}
              className={inputClass}
              disabled={!form.domain_intel_enabled}
            >
              {DAY_NAMES.map((name, i) => (
                <option key={i} value={i}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelClass}>Run at hour (UTC)</label>
            <select
              value={form.domain_intel_hour_utc ?? 7}
              onChange={e => update('domain_intel_hour_utc', Number(e.target.value))}
              className={inputClass}
              disabled={!form.domain_intel_enabled}
            >
              {HOURS.map(h => (
                <option key={h} value={h}>
                  {String(h).padStart(2, '0')}:00 UTC
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Competitors link */}
      <div className={cardClass}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Users size={18} className="text-accent" />
            <div>
              <h2 className="text-base font-medium text-text-bright">Competitors</h2>
              <p className="text-xs text-text/40">
                {competitors ? `${competitors.length} tracked` : 'Loading...'}
              </p>
            </div>
          </div>
          <Link
            to="/competitors/manage"
            className="flex items-center gap-2 px-4 py-2 bg-bg border border-border rounded-lg text-sm text-text-bright hover:border-accent/50 transition-colors"
          >
            Manage Competitors
            <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </div>
  )
}
