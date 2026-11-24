import { useEffect, useRef, type PointerEvent as ReactPointerEvent, type RefObject } from 'react'
import { EntityList } from '../../components/ui/EntityList'
import { EntityRow } from '../../components/ui/EntityRow'
import { useMediaQuery } from '../../components/ui/useMediaQuery'
import type { Filters } from '../dashboard/filters'
import { ArtistLink } from '../artist-history/ArtistLinks'
import { useCountryArtists } from './useCountryArtists'
import { formatMetric, useMetric } from '../../app/metric'

export type CountryArtistsPanelProps = {
  code: string | null
  filters: Filters
  onClose: () => void
  /** Where focus goes on close when the opener (e.g. a canvas click) was not focusable. */
  restoreFocusRef?: RefObject<HTMLElement | null>
}

/** Mobile breakpoint: keep in sync with `@media (max-width: 720px)` in entity-list.css (docs/ui-style-guide.md). */
export const SHEET_QUERY = '(max-width: 720px)'
const SWIPE_CLOSE_PX = 64
// A tap on the map opens the sheet on touchend; the browser's compatibility click for that same tap then lands on the
// freshly mounted backdrop. Backdrop clicks inside this window are that ghost click, not a tap outside.
const GHOST_CLICK_MS = 500



function countryName(code: string): string {
  try { return new Intl.DisplayNames(['en'], { type: 'region' }).of(code) ?? code } catch { return code }
}

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

// Renders nothing without a code. Wide screens: side panel (role=complementary) beside the map.
// <=720px: modal bottom sheet (role=dialog, aria-modal) with backdrop, page scroll lock, focus trap,
// swipe-down/tap-outside/close button/Escape. Focus moves in on open and back to the opener on close.
export function CountryArtistsPanel({ code, filters, onClose, restoreFocusRef }: CountryArtistsPanelProps) {
  return code ? <Panel code={code} filters={filters} onClose={onClose} restoreFocusRef={restoreFocusRef} /> : null
}

function Panel({ code, filters, onClose, restoreFocusRef }: { code: string; filters: Filters; onClose: () => void; restoreFocusRef?: RefObject<HTMLElement | null> }) {
  const ref = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose })
  const sheet = useMediaQuery(SHEET_QUERY)
  const openedAt = useRef(0)
  useEffect(() => { openedAt.current = performance.now() }, [])
  const query = useCountryArtists(code, filters)

  useEffect(() => {
    const active = document.activeElement
    const opener = active instanceof HTMLElement && active !== document.body ? active : null
    ref.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); onCloseRef.current() }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      // The ref points at a long-lived section, so reading it at cleanup time is intended.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      const target = opener?.isConnected ? opener : restoreFocusRef?.current
      target?.focus({ preventScroll: true })
    }
    // restoreFocusRef is a stable ref object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Sheet only: lock page scroll behind it and keep Tab inside it.
  useEffect(() => {
    if (!sheet) return
    const root = document.documentElement
    const previous = [document.body.style.overflow, root.style.overflow]
    document.body.style.overflow = 'hidden'
    root.style.overflow = 'hidden'
    const onTab = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !ref.current) return
      const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (!items.length) { event.preventDefault(); ref.current.focus(); return }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || active === ref.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (active === last || !ref.current.contains(active))) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onTab)
    return () => {
      document.removeEventListener('keydown', onTab)
      document.body.style.overflow = previous[0]
      root.style.overflow = previous[1]
    }
  }, [sheet])

  const metric = useMetric()
  const drag = useRef<number | null>(null)
  const onHandleDown = (event: ReactPointerEvent) => { drag.current = event.clientY; event.currentTarget.setPointerCapture?.(event.pointerId) }
  const onHandleMove = (event: ReactPointerEvent) => {
    if (drag.current === null || !ref.current) return
    ref.current.style.transform = `translateY(${Math.max(0, event.clientY - drag.current)}px)`
  }
  const onHandleUp = (event: ReactPointerEvent) => {
    if (drag.current === null) return
    const distance = event.clientY - drag.current
    drag.current = null
    if (ref.current) ref.current.style.transform = ''
    if (distance > SWIPE_CLOSE_PX) onClose()
  }

  const name = countryName(code)
  const items = query.data?.items ?? []
  const total = items.reduce((sum, item) => sum + item.listens, 0)
  const panel = <aside ref={ref} tabIndex={-1} className="country-panel" role={sheet ? 'dialog' : 'complementary'} aria-modal={sheet ? true : undefined} aria-label={`Artists from ${name}`} aria-busy={query.isPending}>
    {sheet && <div className="sheet-handle" aria-hidden="true" onPointerDown={onHandleDown} onPointerMove={onHandleMove} onPointerUp={onHandleUp} onPointerCancel={onHandleUp}><span /></div>}
    <header className="country-panel-header">
      <div><h3>{name}</h3>{query.data && items.length > 0 && <p className="entity-secondary">{formatMetric(total, metric)}{metric === 'listens' && ' listens'}</p>}</div>
      <button type="button" className="country-panel-close" onClick={onClose} aria-label={`Close ${name} panel`}><svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false"><path d="M2 2l8 8M10 2l-8 8" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" /></svg></button>
    </header>
    {query.isPending && <p role="status" className="entity-empty">Loading artists…</p>}
    {query.isError && <div role="alert" className="entity-empty"><p>Could not load artists.</p><button type="button" onClick={() => void query.refetch()}>Retry</button></div>}
    {query.data && <EntityList label={`Artists from ${name}`} maxHeight="none" empty="No artists for this country in this range.">
      {items.map((item, index) => <EntityRow key={item.key} rank={index + 1} label={item.label || 'Missing metadata'} labelContent={item.label ? <ArtistLink artist={{ key: item.key, label: item.label }} /> : undefined} src={item.image_url} value={formatMetric(item.listens, metric)} />)}
    </EntityList>}
  </aside>
  return sheet ? <><div className="sheet-backdrop" aria-hidden="true" onClick={() => { if (performance.now() - openedAt.current > GHOST_CLICK_MS) onClose() }} />{panel}</> : <div className="country-panel-slot">{panel}</div>
}
