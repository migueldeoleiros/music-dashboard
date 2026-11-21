import { useQuery } from '@tanstack/react-query'
import { fetchHealth } from '../../api/client'

export function SyncStatus() {
  const health = useQuery({ queryKey: ['health'], queryFn: ({ signal }) => fetchHealth(signal), retry: false, refetchInterval: 60_000 })
  if (health.isPending) return <p className="sync-status">Checking sync status…</p>
  if (health.isError) return <p className="sync-status error">Sync status unavailable: {health.error.message}</p>
  const data = health.data
  const lag = data.sync_lag_seconds === null ? 'unknown' : `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(data.sync_lag_seconds / 60)} min`
  return <p className="sync-status">{data.mode === 'fixture' ? 'Synthetic fixture' : 'Live data'} · Sync {data.status === 'degraded' ? 'degraded' : 'OK'} · Lag: {lag}{data.last_updated ? ` · Last import: ${new Date(data.last_updated).toLocaleString(undefined, { timeZone: 'UTC' })} UTC` : ' · No successful import yet'}</p>
}
