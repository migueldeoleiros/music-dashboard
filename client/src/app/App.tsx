import { useCallback, useEffect, useState } from 'react'
import { ChartPanel } from '../features/dashboard/ChartPanel'
import { Controls } from '../features/dashboard/Controls'
import { clampToToday, readFilters, utcFallback, validateFilters, writeFilters, type Filters } from '../features/dashboard/filters'
import { SyncStatus } from '../components/ui/SyncStatus'

export function App() {
  const [filters, setFilters] = useState(() => readFilters(window.location.search))
  const [timezoneNotice, setTimezoneNotice] = useState('')
  const apply = useCallback((next: Filters) => {
    const clamped = clampToToday(next)
    window.history.pushState(null, '', writeFilters(clamped))
    setFilters(clamped)
    setTimezoneNotice('')
  }, [])
  const fallbackTimezone = useCallback(() => {
    setFilters(previous => {
      if (previous.tz === 'UTC') return previous
      const next = utcFallback(previous)
      window.history.replaceState(null, '', writeFilters(next))
      return next
    })
    setTimezoneNotice('The API rejected the selected timezone. Showing UTC instead. Dates after today in UTC are clamped, including From when needed to preserve a valid range. Choose another timezone or reset to your browser timezone.')
  }, [])
  useEffect(() => {
    window.history.replaceState(null, '', writeFilters(filters))
    const onPopState = () => { setFilters(readFilters(window.location.search)); setTimezoneNotice('') }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
    // Initial normalization only; subsequent navigation uses apply / popstate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const error = validateFilters(filters)
  const shared = { filters, onTimezoneRejected: fallbackTimezone }
  return <main className="app">
    <header className="masthead"><div><p className="eyebrow">LISTENING, IN PERSPECTIVE</p><h1>Music Dashboard<span aria-hidden="true">.</span></h1><p className="intro">The artists, places and patterns behind your listening.</p></div><span className="read-only">PUBLIC · READ ONLY</span></header>
    <nav className="section-nav" aria-label="Dashboard sections"><a href="#rankings">Top listens</a><a href="#activity">Activity & places</a><a href="#evolution">Evolution</a><a href="#shares">Shares</a></nav>
    <SyncStatus />
    <Controls key={`${filters.from}/${filters.to}/${filters.tz}`} filters={filters} onChange={apply} />
    {timezoneNotice && <p className="notice" role="status">{timezoneNotice}</p>}
    {import.meta.env.VITE_MOCK_API === 'true' && <p className="notice">Standalone synthetic preview — no API or music-service requests. <a href="?from=2026-01-05&to=2026-01-05&tz=UTC&fixture=partial">Load foundation sample</a> · <a href="?from=2026-01-05&to=2026-01-05&tz=UTC&fixture=full">100% coverage</a> · <a href="?from=2026-01-05&to=2026-01-05&tz=UTC&fixture=empty">Empty sample</a></p>}
    {error ? <p className="error" role="alert">{error} Adjust the shared filters above.</p> : <>
      <section id="rankings" aria-labelledby="rankings-heading"><h2 className="section-heading" id="rankings-heading">On repeat <span>Top 10 · weighted listens</span></h2><div className="ranking-grid">{(['artists', 'albums', 'tracks', 'genres'] as const).map(dimension => <ChartPanel key={dimension} path={`/api/rankings/${dimension}`} title={`Top ${dimension}`} subtitle="Share of included listens" {...shared} />)}</div></section>
      <section id="activity" aria-labelledby="activity-heading"><h2 className="section-heading" id="activity-heading">The bigger picture <span>Every day, everywhere</span></h2><div className="activity-grid"><ChartPanel path="/api/heatmap" title="Daily listening" subtitle="All listens · weeks start Monday" {...shared} /><ChartPanel path="/api/countries" title="Artist origins" subtitle="Artist-associated country, not nationality" {...shared} /></div></section>
      <section id="evolution" aria-labelledby="evolution-heading"><h2 className="section-heading" id="evolution-heading">Evolving taste <span>Top 8 across the whole range + Other</span></h2><div className="chart-grid">{(['artists', 'genres'] as const).map(dimension => <ChartPanel key={dimension} path={`/api/evolution/${dimension}`} title={`${dimension === 'artists' ? 'Artist' : 'Genre'} evolution`} subtitle="Per bucket, never cumulative · Other = known categories outside top 8" {...shared} />)}</div></section>
      <section id="shares" aria-labelledby="shares-heading"><h2 className="section-heading" id="shares-heading">In the mix <span>Top 10 + Other</span></h2><div className="chart-grid">{(['artists', 'genres'] as const).map(dimension => <ChartPanel key={dimension} path={`/api/pies/${dimension}`} title={`${dimension === 'artists' ? 'Artist' : 'Genre'} shares`} subtitle="Included listens only · Other never means unknown metadata" {...shared} />)}</div></section>
    </>}
    <footer className="site-footer">ListenBrainz is the source of truth. Metadata can change historically. Shared links reproduce dates and timezone, not a frozen snapshot. Public aggregates can reveal listening patterns.</footer>
  </main>
}
