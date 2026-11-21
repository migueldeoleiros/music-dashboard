import { useState, type ReactNode } from 'react'
import { ALL_TIME_FROM, clampToToday, presetRange, todayInZone, validateFilters, type Filters, type Period, type Preset } from './filters'

const presets: { key: Preset; label: string }[] = [
  { key: 'today', label: 'Today' }, { key: '7d', label: '7 days' }, { key: '30d', label: '30 days' },
  { key: 'week', label: 'This week' }, { key: 'month', label: 'This month' }, { key: 'year', label: 'This year' }, { key: 'all', label: 'All time' },
]

type Props = { filters: Filters; period: Period; onChange: (filters: Filters, period: Period) => void; children?: ReactNode }

export function Controls({ filters, period, onChange, children }: Props) {
  const [customOpen, setCustomOpen] = useState(period === 'custom')
  // All time uses a lower bound that is never shown: an empty From means "from the beginning".
  const shown = (from: string) => (from === ALL_TIME_FROM ? '' : from)
  const [draft, setDraft] = useState({ from: shown(filters.from), to: filters.to })
  const [error, setError] = useState<string | null>(null)
  const selected: Period = customOpen ? 'custom' : period
  const today = todayInZone(filters.tz)
  return <section className="controls" aria-label="Period">
    <div className="controls-bar">
      <div className="preset-row" role="group" aria-label="Period presets">
        {presets.map(preset => <button type="button" className="pill" key={preset.key} aria-pressed={selected === preset.key}
          onClick={() => { setCustomOpen(false); setError(null); onChange(presetRange(preset.key, filters.tz), preset.key) }}>{preset.label}</button>)}
        <button type="button" className="pill" aria-pressed={selected === 'custom'} aria-expanded={customOpen} onClick={() => setCustomOpen(true)}>Custom</button>
      </div>
      <div className="controls-meta">{children}</div>
    </div>
    {customOpen && <form className="custom-range" onSubmit={event => {
      event.preventDefault()
      const next = clampToToday({ from: draft.from || ALL_TIME_FROM, to: draft.to, tz: filters.tz })
      const message = validateFilters(next)
      setError(message)
      if (!message) onChange(next, 'custom')
    }}>
      <label>From <input type="date" value={draft.from} max={draft.to || today} onChange={event => setDraft(previous => ({ ...previous, from: event.target.value }))} /></label>
      <label>To <input type="date" required value={draft.to} min={draft.from || undefined} max={today} onChange={event => setDraft(previous => ({ ...previous, to: event.target.value }))} /></label>
      <button className="primary" type="submit">Apply</button>
    </form>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>
}
