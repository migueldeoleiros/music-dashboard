import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, fetchSummary } from '../../api/client'
import type { Filters } from './filters'

const number = new Intl.NumberFormat()

type Props = { filters: Filters; onTimezoneRejected: () => void }

// Whole-range totals: only the date range and timezone matter, never the Plays/Minutes metric.
export function SummaryStats({ filters, onTimezoneRejected }: Props) {
  const { from, to, tz } = filters
  const query = useQuery({ queryKey: ['/api/summary', { from, to, tz }], queryFn: ({ signal }) => fetchSummary({ from, to, tz }, signal), retry: false })
  useEffect(() => { if (query.error instanceof ApiError && query.error.timezoneRejected && tz !== 'UTC') onTimezoneRejected() }, [query.error, tz, onTimezoneRejected])
  const data = query.data
  const stats = [
    ['Minutes', data && Math.round(data.minutes)],
    ['Plays', data?.plays],
    ['Tracks', data?.tracks],
    ['Artists', data?.artists],
  ] as const
  // Unknown values (loading or failed) show a dash, never a stale or fake zero.
  return <dl className="summary-stats" aria-label="Listening totals" aria-busy={query.isPending}>
    {stats.map(([label, value]) => <div className="summary-stat" key={label}>
      <dt className="card-title">{label}</dt>
      <dd className="summary-value">{value === undefined ? '—' : number.format(value)}</dd>
    </div>)}
  </dl>
}
