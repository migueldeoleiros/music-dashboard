import { useCallback, useEffect, useRef, useState } from 'react'
import { ChartPanel } from '../features/dashboard/ChartPanel'
import { useChartQuery } from '../features/dashboard/ChartSection'
import { Controls } from '../features/dashboard/Controls'
import { browserTimezone, presetRange, readFilters, readPeriod, rollingYearRange, utcFallback, validateFilters, writeFilters, type Filters, type Period } from '../features/dashboard/filters'
import { CountryArtistsPanel } from '../features/origin-map/CountryArtistsPanel'
import { CountryRankingPanel } from '../features/origin-map/CountryRankingPanel'
import { CoverageNote } from '../components/ui/CoverageNote'
import { SyncStatus } from '../components/ui/SyncStatus'
import { RefreshControl } from '../components/ui/RefreshControl'

type State = { filters: Filters; period: Period }
// The request timezone always comes from the browser; it is never read from or written to the URL.
// After the API rejects it, the visible UTC fallback is kept in memory for later navigation in this page session.
const readState = (tz: string = browserTimezone()): State => {
  const filters = readFilters(window.location.search, tz)
  return { filters, period: readPeriod(window.location.search, filters) }
}

// Coverage is identical across views for one range, so reuse the artists ranking's cache entry.
function PeriodCoverage({ filters }: { filters: Filters }) {
  const query = useChartQuery('/api/rankings/artists', filters)
  return query.data ? <CoverageNote coverage={query.data.coverage} /> : null
}

export function App() {
  const [{ filters, period }, setState] = useState(() => readState())
  const zone = useRef(browserTimezone())
  const [timezoneNotice, setTimezoneNotice] = useState('')
  // Kept across period changes: the panel refetches with the new shared filters (an empty range shows its empty state).
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null)
  const originsRef = useRef<HTMLElement>(null)
  const closeCountry = useCallback(() => setSelectedCountry(null), [])
  const apply = useCallback((next: Filters, nextPeriod: Period) => {
    window.history.pushState(null, '', writeFilters(next, window.location.search, nextPeriod))
    setState({ filters: next, period: nextPeriod })
    setTimezoneNotice('')
  }, [])
  const fallbackTimezone = useCallback(() => {
    zone.current = 'UTC'
    setState(previous => {
      if (previous.filters.tz === 'UTC') return previous
      const next = previous.period === 'custom' ? utcFallback(previous.filters) : presetRange(previous.period, 'UTC')
      window.history.replaceState(null, '', writeFilters(next, window.location.search, previous.period))
      return { filters: next, period: previous.period }
    })
    setTimezoneNotice('The API does not support your browser timezone, so dates are shown in UTC.')
  }, [])
  useEffect(() => {
    window.history.replaceState(null, '', writeFilters(filters, window.location.search, period))
    const onPopState = () => {
      const next = readState(zone.current)
      window.history.replaceState(null, '', writeFilters(next.filters, window.location.search, next.period))
      setState(next)
      setTimezoneNotice('')
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
    // Initial normalization only; subsequent navigation uses apply / popstate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const error = validateFilters(filters)
  const shared = { filters, onTimezoneRejected: fallbackTimezone }
  // Own filters object: rolling 365 days ending today in the visitor tz; the period never reaches it.
  const heatmap = { filters: rollingYearRange(filters.tz), onTimezoneRejected: fallbackTimezone }
  return <main className="app">
    <ChartPanel path="/api/heatmap" label="Daily listening" variant="heatmap" {...heatmap} />
    <Controls filters={filters} period={period} onChange={apply}>
      {!error && <PeriodCoverage filters={filters} />}
      <SyncStatus />
      <RefreshControl />
    </Controls>
    {timezoneNotice && <p className="notice" role="status">{timezoneNotice}</p>}
    {import.meta.env.VITE_MOCK_API === 'true' && <p className="notice">Standalone synthetic preview — no API or music-service requests. <a href="?from=2026-01-05&to=2026-01-05&tz=UTC&fixture=partial">Load foundation sample</a> · <a href="?from=2026-01-05&to=2026-01-05&tz=UTC&fixture=full">100% coverage</a> · <a href="?from=2026-01-05&to=2026-01-05&tz=UTC&fixture=empty">Empty sample</a></p>}
    {error ? <p className="error" role="alert">{error} Adjust the period above.</p> : <>
      <section id="rankings" className="dash-section" aria-label="Rankings"><div className="grid-4">{(['artists', 'albums', 'tracks', 'genres'] as const).map(dimension => <ChartPanel key={dimension} path={`/api/rankings/${dimension}`} label={`Top ${dimension}`} {...shared} />)}</div></section>
      <section id="origins" ref={originsRef} tabIndex={-1} className="dash-section" aria-label="Artist origins">
        <div className="origins-layout has-panel">
          <ChartPanel path="/api/countries" label="Artist origins" selectedCountry={selectedCountry} onCountrySelect={setSelectedCountry} {...shared} />
          {selectedCountry ? <CountryArtistsPanel code={selectedCountry} filters={filters} onClose={closeCountry} restoreFocusRef={originsRef} /> : <CountryRankingPanel filters={filters} />}
        </div>
      </section>
      {/* A single calendar day has no evolution to show: both charts are hidden (and not fetched). */}
      {filters.from !== filters.to && <section id="evolution" className="dash-section" aria-label="Evolution"><div className="grid-2">{(['artists', 'genres'] as const).map(dimension => <ChartPanel key={dimension} path={`/api/evolution/${dimension}`} label={`${dimension === 'artists' ? 'Artist' : 'Genre'} evolution`} {...shared} />)}</div></section>}
      <section id="shares" className="dash-section" aria-label="Shares"><div className="grid-2">{(['artists', 'genres'] as const).map(dimension => <ChartPanel key={dimension} path={`/api/pies/${dimension}`} label={`${dimension === 'artists' ? 'Artist' : 'Genre'} shares`} {...shared} />)}</div></section>
    </>}
  </main>
}
