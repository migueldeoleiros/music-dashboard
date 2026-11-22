import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ALL_TIME_FROM, presetRange, todayInZone, validateFilters, type Filters, type Period, type Preset } from './filters'

const presets: { key: Preset; label: string }[] = [
  { key: 'today', label: 'Today' }, { key: '7d', label: '7 days' }, { key: '30d', label: '30 days' },
  { key: 'week', label: 'This week' }, { key: 'month', label: 'This month' }, { key: 'year', label: 'This year' }, { key: 'all', label: 'All time' },
]

type Props = { filters: Filters; period: Period; onChange: (filters: Filters, period: Period) => void; children?: ReactNode }

export function Controls({ filters, period, onChange, children }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const opener = useRef<HTMLButtonElement>(null)
  const fromInput = useRef<HTMLInputElement>(null)
  const toInput = useRef<HTMLInputElement>(null)
  const id = useId()
  const [draft, setDraft] = useState({ from: '', to: '' })
  const [error, setError] = useState<{ message: string; field: 'from' | 'to' } | null>(null)
  const today = todayInZone(filters.tz)
  const close = () => dialog.current?.close()
  useEffect(() => {
    // Back/forward discards an uncommitted draft, just like Cancel.
    const onPopState = () => dialog.current?.close()
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])
  return <section className="controls" aria-label="Period">
    <div className="controls-bar">
      <div className="preset-row" role="group" aria-label="Period presets">
        {presets.map(preset => <button type="button" className="pill" key={preset.key} aria-pressed={period === preset.key}
          onClick={() => onChange(presetRange(preset.key, filters.tz), preset.key)}>{preset.label}</button>)}
        <button ref={opener} type="button" className="pill" aria-pressed={period === 'custom'} aria-haspopup="dialog" aria-controls={id} onClick={() => {
          // All time's sentinel is never displayed: blank From means "from the beginning".
          setDraft({ from: filters.from === ALL_TIME_FROM ? '' : filters.from, to: filters.to })
          setError(null)
          dialog.current?.showModal()
        }}>Custom</button>
      </div>
      <div className="controls-meta">{children}</div>
    </div>
    <dialog ref={dialog} id={id} className="custom-dialog" aria-labelledby={`${id}-title`} aria-describedby={`${id}-help`} onClose={() => opener.current?.focus()}>
      <form className="custom-range" noValidate onSubmit={event => {
        event.preventDefault()
        const next = { from: draft.from || ALL_TIME_FROM, to: draft.to, tz: filters.tz }
        // An unfinished native date has an empty value too, but is not an intentionally blank From.
        const badFrom = fromInput.current?.validity.badInput
        const badTo = toInput.current?.validity.badInput
        const message = badFrom || badTo ? 'Enter valid local dates in YYYY-MM-DD format.'
          : validateFilters(next) ?? (next.to > today ? 'To must be today or earlier.' : null)
        setError(message ? { message, field: badFrom ? 'from' : 'to' } : null)
        if (!message) { close(); onChange(next, 'custom') }
      }}>
        <h2 id={`${id}-title`}>Custom timeframe</h2>
        <p id={`${id}-help`}>Dates use {filters.tz}. Leave From blank to start at the beginning. Applied dates stay fixed.</p>
        <div className="custom-dates">
          <label htmlFor={`${id}-from`}>From</label>
          <input ref={fromInput} id={`${id}-from`} type="date" min="0001-01-01" value={draft.from} max={draft.to || today} aria-invalid={error?.field === 'from'} aria-describedby={error?.field === 'from' ? `${id}-error` : `${id}-help`} onChange={event => setDraft(previous => ({ ...previous, from: event.target.value }))} />
          <label htmlFor={`${id}-to`}>To</label>
          <input ref={toInput} id={`${id}-to`} type="date" required min={draft.from || '0001-01-01'} max={today} value={draft.to} aria-invalid={error?.field === 'to'} aria-describedby={error?.field === 'to' ? `${id}-error` : `${id}-help`} onChange={event => setDraft(previous => ({ ...previous, to: event.target.value }))} />
        </div>
        {error && <p id={`${id}-error`} role="alert" className="error">{error.message}</p>}
        <div className="custom-actions">
          <button type="button" onClick={close}>Cancel</button>
          <button className="primary" type="submit">Apply</button>
        </div>
      </form>
    </dialog>
  </section>
}
