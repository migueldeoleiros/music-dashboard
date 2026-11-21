import { useQuery } from '@tanstack/react-query'
import { fetchHealth } from '../../api/client'

const stamp = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

// Compact "last updated" line; lag and mode details live in the title.
export function SyncStatus() {
  const health = useQuery({ queryKey: ['health'], queryFn: ({ signal }) => fetchHealth(signal), retry: false, refetchInterval: 60_000 })
  if (health.isPending) return null
  if (health.isError) return <p className="sync-status error" title={health.error.message}>Sync status unavailable</p>
  const data = health.data
  const lag = data.sync_lag_seconds === null ? 'unknown' : `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(data.sync_lag_seconds / 60)} min`
  const updated = data.last_updated ? <>Updated <time dateTime={data.last_updated}>{stamp.format(new Date(data.last_updated))}</time></> : 'No import yet'
  return <p className="sync-status" title={`${data.mode === 'fixture' ? 'Synthetic fixture' : 'Live data'} · Sync lag: ${lag}`}>
    {data.mode === 'fixture' && 'Synthetic · '}{data.status === 'degraded' && 'Sync degraded · '}{updated}
  </p>
}
