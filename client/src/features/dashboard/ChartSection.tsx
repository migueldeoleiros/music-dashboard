import { useEffect, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, fetchChart, type ChartData, type ChartPath } from '../../api/client'
import { CoverageNote } from '../../components/ui/CoverageNote'
import type { Filters } from './filters'

export function EmptyState() { return <div className="empty-state"><span aria-hidden="true">—</span><p>No listening data available for this view and range.</p><small>Try a wider range. Metadata coverage may still be incomplete.</small></div> }

export type ChartSectionProps = { path: ChartPath; title: string; subtitle: string; filters: Filters; onTimezoneRejected: () => void; children: (data: ChartData) => ReactNode }

// Generic card: fetch, loading/error states, coverage note, fixture tag. The visualization is a render prop.
export function ChartSection({ path, title, subtitle, filters, onTimezoneRejected, children }: ChartSectionProps) {
  const query = useQuery({ queryKey: [path, filters], queryFn: ({ signal }) => fetchChart(path, filters, signal), enabled: !!filters.from && !!filters.to, retry: false })
  useEffect(() => { if (query.error instanceof ApiError && query.error.timezoneRejected && filters.tz !== 'UTC') onTimezoneRejected() }, [query.error, filters.tz, onTimezoneRejected])
  const dimension = query.data && 'dimension' in query.data ? query.data.dimension : path === '/api/heatmap' ? 'listens' : 'countries'
  return <section className={`card ${path.includes('/rankings/') ? 'ranking-card' : ''}`} aria-label={title} aria-busy={query.isPending}>
    <header className="card-heading"><h2>{title}</h2><p>{subtitle}</p></header>
    {query.isPending && <p className="loading" role="status">Loading {title.toLowerCase()}…</p>}
    {query.isError && <div className="error" role="alert"><p>{query.error.message}</p>{query.error instanceof ApiError && query.error.body.details.length > 0 && <ul>{query.error.body.details.map(detail => <li key={detail}>{detail}</li>)}</ul>}<button type="button" onClick={() => { void query.refetch() }}>Retry this view</button></div>}
    {query.data && <>{children(query.data)}<CoverageNote coverage={query.data.coverage} dimension={dimension} lastUpdated={query.data.last_updated} />{query.data.fixture && <span className="fixture-tag">Synthetic fixture</span>}</>}
  </section>
}
