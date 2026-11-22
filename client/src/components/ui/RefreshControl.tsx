import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchRefresh, type RefreshStatus } from '../../api/refresh'
import './refresh.css'

const storageKey = 'music-refresh-request'
const active = (status: RefreshStatus['status']) => ['pending', 'running', 'deferred'].includes(status)
function rememberedRequest() {
  try { return sessionStorage.getItem(storageKey) } catch { return null }
}
function rememberRequest(id: string | null) {
  try { if (id) sessionStorage.setItem(storageKey, id) } catch { /* Storage is optional. */ }
}

function statusText(data: RefreshStatus) {
  switch (data.status) {
    case 'pending': return 'Refresh queued.'
    case 'running': return 'Refreshing recent listens…'
    case 'deferred': return data.detail === 'requires_reconcile' || data.detail === 'awaiting_initial_export'
      ? 'Refresh waiting for scheduled reconciliation. It will expire if not ready in time.'
      : 'Refresh delayed by another sync or retry backoff.'
    case 'succeeded': return 'Refresh complete.'
    case 'failed': return 'Refresh failed. The normal schedule remains active.'
    case 'expired': return 'Refresh expired. The normal schedule remains active.'
    default: return ''
  }
}

export function RefreshControl() {
  const client = useQueryClient()
  const [requestId, setRequestId] = useState(rememberedRequest)
  const reloaded = useRef<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const status = useQuery({
    queryKey: ['refresh'], queryFn: ({ signal }) => fetchRefresh('GET', signal), retry: false,
    refetchInterval: query => {
      const data = query.state.data
      return data?.enabled && (active(data.status) || (data.cooldown_until && Date.parse(data.cooldown_until) > Date.now())) ? 3000 : false
    },
  })
  const admission = useMutation({
    mutationFn: () => fetchRefresh('POST'),
    onMutate: () => client.cancelQueries({ queryKey: ['refresh'] }),
    onSuccess: async data => {
      // A pre-admission GET (including a polling read during POST) must never replace
      // the admitted UUID with an older snapshot and stop its polling.
      await client.cancelQueries({ queryKey: ['refresh'] })
      rememberRequest(data.request_id ?? null)
      setRequestId(data.request_id ?? null)
      client.setQueryData(['refresh'], data)
      await client.invalidateQueries({ queryKey: ['refresh'] })
    },
    onError: () => { void status.refetch() },
  })
  const data = status.data
  useEffect(() => {
    if (!data?.cooldown_until) return
    const remaining = Date.parse(data.cooldown_until) - Date.now()
    if (!Number.isFinite(remaining)) return
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(2_147_483_647, Math.max(0, remaining + 10)))
    return () => window.clearTimeout(timer)
  }, [data?.cooldown_until])
  const superseded = !!requestId && !!data?.request_id && requestId !== data.request_id
  const reload = () => client.invalidateQueries({ predicate: query => query.queryKey[0] !== 'refresh' })
  useEffect(() => {
    if (data?.status === 'succeeded' && data.request_id && data.request_id === requestId && reloaded.current !== requestId) {
      reloaded.current = requestId
      void client.invalidateQueries({ predicate: query => query.queryKey[0] !== 'refresh' })
    }
  }, [client, data, requestId])
  if (status.isPending || (data && !data.enabled)) return null
  if (!data) return <span className="refresh-control"><span role="status">Refresh status unavailable.</span><button type="button" onClick={() => { void status.refetch() }}>Retry status</button></span>
  const busy = active(data.status)
  const cooling = !!data.cooldown_until && Date.parse(data.cooldown_until) > now
  return <span className="refresh-control">
    <button type="button" disabled={admission.isPending || busy || cooling || status.isError} onClick={() => admission.mutate()}>
      {admission.isPending ? 'Requesting…' : 'Refresh listens'}
    </button>
    <span role="status" aria-live="polite">
      {superseded ? 'A newer shared request replaced your refresh. Showing its status. ' : ''}
      {status.isError ? 'Refresh status unavailable. Retry status before requesting again.' : statusText(data)}
      {!busy && cooling && ' Shared cooldown active.'}
    </span>
    {admission.isError && <span role="alert">{admission.error.message}</span>}
    {status.isError && <button type="button" onClick={() => { void status.refetch() }}>Retry status</button>}
    {data.status === 'succeeded' && <button type="button" onClick={() => { void reload() }}>Reload charts</button>}
  </span>
}
