import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, fetchChart, type ChartData, type ChartPath } from '../../api/client'
import { CountryChart, EvolutionChart, HeatmapChart, PieChart, stableColor } from '../../components/charts'
import { CoverageNote } from '../../components/ui/CoverageNote'
import type { Filters } from './filters'

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })

function Visualization({ data }: { data: ChartData }) {
  if ('items' in data) return data.items.length ? <ol className="rankings">{data.items.map((item, index) => <li key={item.key}>
    <span className="rank-number">{String(index + 1).padStart(2, '0')}</span>
    <div className="rank-name"><span>{item.label || `Missing ${data.dimension === 'tracks' ? 'title' : 'metadata'}`}</span>{item.metadata_state === 'missing' && <small>Missing metadata</small>}<div className="rank-bar" style={{ width: `${Math.min(100, Math.max(0, item.share * 100))}%`, background: stableColor(item.key) }} /></div>
    <span className="rank-value">{number.format(item.listens)}<small>{number.format(item.share * 100)}%</small></span>
  </li>)}</ol> : <Empty />
  if ('cells' in data) return data.cells.some(cell => cell.listens > 0) ? <HeatmapChart data={data} /> : <Empty />
  if ('countries' in data) return data.countries.length ? <CountryChart data={data} /> : <Empty />
  if ('buckets' in data) return data.series.length && data.buckets.length ? <EvolutionChart data={data} /> : <Empty />
  return data.slices.length ? <PieChart data={data} /> : <Empty />
}

function Empty() { return <div className="empty-state"><span aria-hidden="true">—</span><p>No listening data available for this view and range.</p><small>Try a wider range. Metadata coverage may still be incomplete.</small></div> }

export function ChartPanel({ path, title, subtitle, filters, onTimezoneRejected }: { path: ChartPath; title: string; subtitle: string; filters: Filters; onTimezoneRejected: () => void }) {
  const query = useQuery({ queryKey: [path, filters], queryFn: ({ signal }) => fetchChart(path, filters, signal), enabled: !!filters.from && !!filters.to, retry: false })
  useEffect(() => { if (query.error instanceof ApiError && query.error.timezoneRejected && filters.tz !== 'UTC') onTimezoneRejected() }, [query.error, filters.tz, onTimezoneRejected])
  const dimension = query.data && 'dimension' in query.data ? query.data.dimension : path === '/api/heatmap' ? 'listens' : 'countries'
  return <section className={`card ${path.includes('/rankings/') ? 'ranking-card' : ''}`} aria-label={title} aria-busy={query.isPending}>
    <header className="card-heading"><h2>{title}</h2><p>{subtitle}</p></header>
    {query.isPending && <p className="loading" role="status">Loading {title.toLowerCase()}…</p>}
    {query.isError && <div className="error" role="alert"><p>{query.error.message}</p>{query.error instanceof ApiError && query.error.body.details.length > 0 && <ul>{query.error.body.details.map(detail => <li key={detail}>{detail}</li>)}</ul>}<button type="button" onClick={() => { void query.refetch() }}>Retry this view</button></div>}
    {query.data && <><Visualization data={query.data} /><CoverageNote coverage={query.data.coverage} dimension={dimension} lastUpdated={query.data.last_updated} />{query.data.fixture && <span className="fixture-tag">Synthetic fixture</span>}</>}
  </section>
}
