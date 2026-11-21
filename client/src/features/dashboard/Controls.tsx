import { useState } from 'react'
import { browserTimezone, changeTimezone, clampToToday, presetRange, todayInZone, validateFilters, type Filters, type Preset } from './filters'

const presets: { key: Preset; label: string }[] = [{ key: 'today', label: 'Today' }, { key: '7d', label: '7 days' }, { key: '30d', label: '30 days' }, { key: 'year', label: 'This year' }, { key: 'all', label: 'All time' }]

export function Controls({ filters, onChange }: { filters: Filters; onChange: (filters: Filters) => void }) {
  const [draft, setDraft] = useState(filters)
  const [error, setError] = useState<string | null>(null)
  const change = (key: keyof Filters, value: string) => setDraft(previous => ({ ...previous, [key]: value }))
  const apply = (next: Filters) => { setDraft(next); setError(null); onChange(next) }
  return <section className="controls" aria-label="Shared dashboard filters">
    <div className="preset-row">{presets.map(preset => <button type="button" key={preset.key} onClick={() => apply(presetRange(preset.key, filters.tz))}>{preset.label}</button>)}</div>
    <form onSubmit={event => {
      event.preventDefault()
      const next = clampToToday({ ...draft, tz: draft.tz.trim() })
      const message = validateFilters(next)
      setError(message)
      if (!message) apply(next)
    }}>
      <label>From <input type="date" required value={draft.from} max={draft.to} onChange={event => change('from', event.target.value)} /></label>
      <label>To <input type="date" required value={draft.to} min={draft.from} max={todayInZone(draft.tz)} onChange={event => change('to', event.target.value)} /></label>
      <label className="timezone-field">Timezone <input type="text" required list="timezones" value={draft.tz} onChange={event => change('tz', event.target.value)} spellCheck={false} autoComplete="off" /></label>
      <datalist id="timezones">{['UTC', 'Europe/Lisbon', 'Europe/London', 'America/New_York', 'Asia/Tokyo', 'Asia/Kathmandu', 'Australia/Sydney', 'Pacific/Apia'].map(tz => <option key={tz} value={tz} />)}</datalist>
      <button className="primary" type="submit">Apply range</button>
      <button type="button" onClick={() => apply(changeTimezone(filters, browserTimezone()))}>Reset to browser</button>
    </form>
    <p className="filter-explainer">{filters.from} → {filters.to} · <strong>{filters.tz}</strong> · inclusive local dates · all views share this range</p>
    {error && <p role="alert" className="error">{error}</p>}
  </section>
}
