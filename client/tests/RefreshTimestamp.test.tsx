import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SyncStatus } from '../src/components/ui/SyncStatus'
import { fetchHealth } from '../src/api/client'
import { fetchRefresh, type RefreshStatus } from '../src/api/refresh'

vi.mock('../src/api/refresh', () => ({ fetchRefresh: vi.fn() }))
vi.mock('../src/api/client', () => ({ fetchHealth: vi.fn() }))
const health = { status: 'ok', mode: 'live', last_updated: '2026-01-06T00:00:00Z', sync_lag_seconds: 60 }
const timestamp = { name: /Updated/ }
let client: QueryClient
const base: RefreshStatus = { enabled: true, request_id: null, status: 'idle', requested_at: null, started_at: null, finished_at: null, cooldown_until: null, retry_after_seconds: null, coalesced: false, detail: null }
beforeEach(() => {
  sessionStorage.clear()
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  vi.mocked(fetchHealth).mockReset().mockResolvedValue(health as never)
  vi.mocked(fetchRefresh).mockReset().mockResolvedValue(base)
})
afterEach(() => { cleanup(); client.clear(); vi.restoreAllMocks() })
function show() { render(<QueryClientProvider client={client}><SyncStatus /></QueryClientProvider>) }
it('keeps a plain timestamp when refresh is disabled', async () => {
  vi.mocked(fetchRefresh).mockResolvedValue({ ...base, enabled: false })
  show()
  await waitFor(() => expect(fetchRefresh).toHaveBeenCalledOnce())
  await screen.findByText(/Updated/)
  expect(screen.queryByRole('button')).toBeNull()
  expect(fetchRefresh).not.toHaveBeenCalledWith('POST', expect.anything())
})
it('coalesces to the returned id, persists correlation, and waits for completion', async () => {
  let posted = false
  vi.mocked(fetchRefresh).mockImplementation(async method => {
    if (method === 'POST') posted = true
    return posted ? { ...base, request_id: 'request-a', status: 'pending', coalesced: true } : base
  })
  show()
  fireEvent.click(await screen.findByRole('button', timestamp))
  await screen.findByText('Refresh queued.')
  expect(sessionStorage.getItem('music-refresh-request')).toBe('request-a')
  expect((screen.getByRole('button', timestamp) as HTMLButtonElement).disabled).toBe(true)
  expect(vi.mocked(fetchRefresh).mock.calls.filter(call => call[0] === 'POST')).toHaveLength(1)
})
it('reloads cached charts only for the correlated completed request and without extra buttons', async () => {
  sessionStorage.setItem('music-refresh-request', 'request-a')
  client.setQueryData(['/api/heatmap', {}], { generation: 1 })
  vi.mocked(fetchRefresh).mockResolvedValue({ ...base, request_id: 'request-a', status: 'succeeded' })
  show()
  await screen.findByText('Refresh complete.')
  await waitFor(() => expect(client.getQueryState(['/api/heatmap', {}])?.isInvalidated).toBe(true))
  expect(screen.queryByRole('button', { name: /Reload charts|Retry status|Refresh listens/ })).toBeNull()
})
it('keeps correlation after reload and never mistakes a different request for its completion', async () => {
  sessionStorage.setItem('music-refresh-request', 'request-a')
  client.setQueryData(['/api/heatmap', {}], { generation: 1 })
  vi.mocked(fetchRefresh).mockResolvedValue({ ...base, request_id: 'request-b', status: 'succeeded' })
  show()
  await screen.findByText(/newer shared request replaced/)
  expect(client.getQueryState(['/api/heatmap', {}])?.isInvalidated).toBe(false)
  expect(screen.queryByRole('button', { name: /Reload charts|Retry status|Refresh listens/ })).toBeNull()
})
it('shows deferred reconciliation, terminal expiry, and bounded cooldown states', async () => {
  vi.mocked(fetchRefresh).mockResolvedValue({ ...base, request_id: 'request-a', status: 'deferred', detail: 'requires_reconcile' })
  show()
  await screen.findByText(/waiting for scheduled reconciliation/)
  client.setQueryData(['refresh'], { ...base, request_id: 'request-a', status: 'expired', cooldown_until: '9999-01-01T00:00:00Z' })
  await screen.findByText(/Refresh expired.*Shared cooldown active/)
  expect((screen.getByRole('button', timestamp) as HTMLButtonElement).disabled).toBe(true)
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
  const button = await screen.findByRole('button', timestamp)
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
  fireEvent.click(await screen.findByRole('button', timestamp))
  await screen.findByText(/could not be requested/, { selector: '[role=status]' })
  expect(vi.mocked(fetchRefresh).mock.calls.filter(call => call[0] === 'POST')).toHaveLength(1)
})

it('shows a plain timestamp when the stale GET fails', async () => {
  vi.mocked(fetchRefresh).mockRejectedValue(new Error('down'))
  show()
  await screen.findByText(/Updated/)
  expect(screen.queryByRole('button')).toBeNull()
})
