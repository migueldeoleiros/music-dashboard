import type { components } from './schema'

export type RefreshStatus = components['schemas']['RefreshStatus']

export async function fetchRefresh(method: 'GET' | 'POST' = 'GET', signal?: AbortSignal): Promise<RefreshStatus> {
  if (import.meta.env.VITE_MOCK_API === 'true') {
    return { enabled: false, request_id: null, status: 'idle', requested_at: null, started_at: null, finished_at: null, cooldown_until: null, retry_after_seconds: null, coalesced: false, detail: null }
  }
  const response = await fetch('/api/refresh', {
    method, signal, credentials: 'same-origin', cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(method === 'POST'
    ? 'Refresh could not be requested. Check status before retrying.'
    : 'Refresh status unavailable. Your charts are still available.')
  return await response.json() as RefreshStatus
}
