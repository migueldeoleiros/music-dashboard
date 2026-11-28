import type { components } from './schema'

export type RefreshStatus = components['schemas']['RefreshStatus']

export async function fetchRefresh(method: 'GET' | 'POST' = 'GET', signal?: AbortSignal): Promise<RefreshStatus> {
  const response = await fetch('/api/refresh', {
    method, signal, credentials: 'same-origin', cache: 'no-store',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(method === 'POST'
    ? 'Refresh could not be requested. Check status before retrying.'
    : 'Refresh status unavailable. Your charts are still available.')
  return await response.json() as RefreshStatus
}
