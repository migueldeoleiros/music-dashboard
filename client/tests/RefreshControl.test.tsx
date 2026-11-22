import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { RefreshControl } from '../src/components/ui/RefreshControl'
import { fetchRefresh, type RefreshStatus } from '../src/api/refresh'

vi.mock('../src/api/refresh', () => ({ fetchRefresh: vi.fn() }))
let client: QueryClient
const base: RefreshStatus = { enabled: true, request_id: null, status: 'idle', requested_at: null, started_at: null, finished_at: null, cooldown_until: null, retry_after_seconds: null, coalesced: false, detail: null }
beforeEach(() => {
  sessionStorage.clear()
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  vi.mocked(fetchRefresh).mockReset().mockResolvedValue(base)
})
afterEach(() => { cleanup(); client.clear(); vi.restoreAllMocks() })
function show() { render(<QueryClientProvider client={client}><RefreshControl /></QueryClientProvider>) }
it('hides disabled refresh without a POST', async () => {
  vi.mocked(fetchRefresh).mockResolvedValue({ ...base, enabled: false })
  show()
  await waitFor(() => expect(fetchRefresh).toHaveBeenCalledOnce())
  expect(screen.queryByRole('button')).toBeNull()
})
it('coalesces to the returned id, persists correlation, and waits for completion', async () => {
  let posted = false
  vi.mocked(fetchRefresh).mockImplementation(async method => {
    if (method === 'POST') posted = true
    return posted ? { ...base, request_id: 'request-a', status: 'pending', coalesced: true } : base
  })
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Refresh listens' }))
  await screen.findByText('Refresh queued.')
  expect(sessionStorage.getItem('music-refresh-request')).toBe('request-a')
  expect((screen.getByRole('button', { name: 'Refresh listens' }) as HTMLButtonElement).disabled).toBe(true)
  expect(vi.mocked(fetchRefresh).mock.calls.filter(call => call[0] === 'POST')).toHaveLength(1)
})
it('reloads cached charts only for the correlated completed request and supports explicit reload', async () => {
  sessionStorage.setItem('music-refresh-request', 'request-a')
  client.setQueryData(['/api/heatmap', {}], { generation: 1 })
  vi.mocked(fetchRefresh).mockResolvedValue({ ...base, request_id: 'request-a', status: 'succeeded' })
  show()
  await screen.findByText('Refresh complete.')
  await waitFor(() => expect(client.getQueryState(['/api/heatmap', {}])?.isInvalidated).toBe(true))
  expect(screen.getByRole('button', { name: 'Reload charts' })).toBeTruthy()
})
it('keeps correlation after reload and never mistakes a different request for its completion', async () => {
  sessionStorage.setItem('music-refresh-request', 'request-a')
  client.setQueryData(['/api/heatmap', {}], { generation: 1 })
  vi.mocked(fetchRefresh).mockResolvedValue({ ...base, request_id: 'request-b', status: 'succeeded' })
  show()
  await screen.findByText(/newer shared request replaced/)
  expect(client.getQueryState(['/api/heatmap', {}])?.isInvalidated).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Reload charts' }))
  await waitFor(() => expect(client.getQueryState(['/api/heatmap', {}])?.isInvalidated).toBe(true))
})
it('shows deferred reconciliation, terminal expiry, and bounded cooldown states', async () => {
  vi.mocked(fetchRefresh).mockResolvedValue({ ...base, request_id: 'request-a', status: 'deferred', detail: 'requires_reconcile' })
  show()
  await screen.findByText(/waiting for scheduled reconciliation/)
  client.setQueryData(['refresh'], { ...base, request_id: 'request-a', status: 'expired', cooldown_until: '9999-01-01T00:00:00Z' })
  await screen.findByText(/Refresh expired.*Shared cooldown active/)
  expect((screen.getByRole('button', { name: 'Refresh listens' }) as HTMLButtonElement).disabled).toBe(true)
})
it('ignores a pre-admission GET resolving last and keeps polling the admitted UUID to completion', async () => {
  let reads = 0
  let resolveOld: ((data: RefreshStatus) => void) | undefined
  const oldRead = new Promise<RefreshStatus>(resolve => { resolveOld = resolve })
  const pending: RefreshStatus = { ...base, request_id: 'request-a', status: 'pending' }
  vi.mocked(fetchRefresh).mockImplementation(async method => {
    if (method === 'POST') return pending
    reads += 1
    if (reads === 1) return base
    if (reads === 2) return oldRead
    if (reads === 3) return pending // fresh reconciliation after POST
    return { ...pending, status: 'succeeded' } // next automatic polling tick
  })
  client.setQueryData(['/api/heatmap', {}], { generation: 1 })
  show()
  const button = await screen.findByRole('button', { name: 'Refresh listens' })
  void client.refetchQueries({ queryKey: ['refresh'] })
  await waitFor(() => expect(reads).toBe(2))
  fireEvent.click(button)
  await screen.findByText('Refresh queued.')
  await waitFor(() => expect(reads).toBe(3))
  await act(async () => { resolveOld?.(base); await oldRead })
  expect(client.getQueryData<RefreshStatus>(['refresh'])?.request_id).toBe('request-a')
  expect(client.getQueryData<RefreshStatus>(['refresh'])?.status).toBe('pending')
  await screen.findByText('Refresh complete.', {}, { timeout: 4500 })
  await waitFor(() => expect(client.getQueryState(['/api/heatmap', {}])?.isInvalidated).toBe(true))
})

it('handles admission uncertainty without automatic POST retries', async () => {
  vi.mocked(fetchRefresh).mockImplementation(async method => {
    if (method === 'POST') throw new Error('Refresh could not be requested. Check status before retrying.')
    return base
  })
  show()
  fireEvent.click(await screen.findByRole('button', { name: 'Refresh listens' }))
  await screen.findByRole('alert')
  expect(vi.mocked(fetchRefresh).mock.calls.filter(call => call[0] === 'POST')).toHaveLength(1)
})
