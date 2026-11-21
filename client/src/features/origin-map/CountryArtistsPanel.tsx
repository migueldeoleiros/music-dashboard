import { useEffect, useRef } from 'react'
import { EntityList } from '../../components/ui/EntityList'
import { EntityRow } from '../../components/ui/EntityRow'
import type { Filters } from '../dashboard/filters'
import { useCountryArtists } from './useCountryArtists'

export type CountryArtistsPanelProps = { code: string | null; filters: Filters; onClose: () => void }

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

function countryName(code: string): string {
  try { return new Intl.DisplayNames(['en'], { type: 'region' }).of(code) ?? code } catch { return code }
}

// Renders nothing without a code. Side panel (role=complementary); focus moves in on open and back to
// the opener on close. Narrow-screen bottom sheet is a later lane: it only needs to restyle
// `.country-panel` inside the single breakpoint in entity-list.css.
export function CountryArtistsPanel({ code, filters, onClose }: CountryArtistsPanelProps) {
  return code ? <Panel code={code} filters={filters} onClose={onClose} /> : null
}

function Panel({ code, filters, onClose }: { code: string; filters: Filters; onClose: () => void }) {
  const ref = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose })
  const query = useCountryArtists(code, filters)

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    ref.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); onCloseRef.current() }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener?.isConnected) opener.focus()
    }
  }, [])

  const name = countryName(code)
  const items = query.data?.items ?? []
  const total = items.reduce((sum, item) => sum + item.listens, 0)
  return <aside ref={ref} tabIndex={-1} className="country-panel" role="complementary" aria-label={`Artists from ${name}`} aria-busy={query.isPending}>
    <header className="country-panel-header">
      <div><h3>{name}</h3>{query.data && items.length > 0 && <p className="entity-secondary">{number.format(total)} listens</p>}</div>
      <button type="button" className="country-panel-close" onClick={onClose} aria-label={`Close ${name} panel`}>×</button>
    </header>
    {query.isPending && <p role="status" className="entity-empty">Loading artists…</p>}
    {query.isError && <div role="alert" className="entity-empty"><p>Could not load artists.</p><button type="button" onClick={() => void query.refetch()}>Retry</button></div>}
    {query.data && <EntityList label={`Artists from ${name}`} maxHeight="min(60vh, 28rem)" empty="No artists for this country in this range.">
      {items.map((item, index) => <EntityRow key={item.key} rank={index + 1} label={item.label || 'Missing metadata'} src={item.image_url} value={number.format(item.listens)} />)}
    </EntityList>}
  </aside>
}
