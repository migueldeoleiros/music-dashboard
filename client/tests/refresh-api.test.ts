import { afterEach, expect, it, vi } from 'vitest'
import { fetchRefresh } from '../src/api/refresh'

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })
it('uses only relative same-origin no-store requests, never upstream', async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ enabled: true, status: 'pending' }), { status: 202 }))
  vi.stubGlobal('fetch', fetcher)
  await fetchRefresh('POST')
  expect(fetcher).toHaveBeenCalledWith('/api/refresh', {
    method: 'POST', signal: undefined, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' },
  })
  expect(fetcher).toHaveBeenCalledOnce()
})
it('does not expose private error bodies or automatically retry admission', async () => {
  const fetcher = vi.fn(async () => new Response('private database detail', { status: 503 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(fetchRefresh('POST')).rejects.toThrow('Check status before retrying')
  expect(fetcher).toHaveBeenCalledOnce()
})
