import { useEffect, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, fetchChart, type ChartData, type ChartPath } from '../../api/client'
import { Panel } from '../../components/ui/Panel'
import type { Filters } from './filters'

export function EmptyState() { return <div className="empty-state"><span aria-hidden="true">—</span><p>No listening data available for this view and range.</p><small>Try a wider range. Metadata coverage may still be incomplete.</small></div> }

// Shared by sections and by the period bar (coverage indicator reuses a section's cache entry).
export function useChartQuery(path: ChartPath, filters: Filters, enabled = true) {
  return useQuery({ queryKey: [path, filters], queryFn: ({ signal }) => fetchChart(path, filters, signal), enabled: enabled && !!filters.from && !!filters.to, retry: false })
}

export type ChartSectionProps = {
  path: ChartPath
  /** Concise card title: rendered as a heading right above (outside) the card and used as its accessible name. */
  label: string
  /** Explicit filters: the heatmap passes its own rolling window instead of the shared period. */
  filters: Filters
  variant?: 'default' | 'heatmap'
  onTimezoneRejected: () => void
  children: (data: ChartData) => ReactNode
}

// Generic card: fetch, loading/error states, fixture tag. The visualization is a render prop.
export function ChartSection({ path, label, filters, variant = 'default', onTimezoneRejected, children }: ChartSectionProps) {
  const query = useChartQuery(path, filters)
  useEffect(() => { if (query.error instanceof ApiError && query.error.timezoneRejected && filters.tz !== 'UTC') onTimezoneRejected() }, [query.error, filters.tz, onTimezoneRejected])
  return <Panel title={label} variant={variant === 'heatmap' ? 'heatmap' : path.includes('/rankings/') ? 'ranking' : 'default'} busy={query.isPending}>
    {query.isPending && <p className="loading" role="status">Loading {label.toLowerCase()}…</p>}
    {query.isError && <div className="error" role="alert"><p>{query.error.message}</p>{query.error instanceof ApiError && query.error.body.details.length > 0 && <ul>{query.error.body.details.map(detail => <li key={detail}>{detail}</li>)}</ul>}<button type="button" onClick={() => { void query.refetch() }}>Retry this view</button></div>}
    {query.data && children(query.data)}
  </Panel>
}
