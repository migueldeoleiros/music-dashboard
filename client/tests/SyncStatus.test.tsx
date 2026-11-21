import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SyncStatus } from '../src/components/ui/SyncStatus'
import { mockChart } from '../src/api/mock'

let client: QueryClient | undefined
afterEach(() => { cleanup(); client?.clear(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })
function showStatus(status: 'ok' | 'degraded', lag: number | null, updated: string | null) {
  vi.stubEnv('VITE_MOCK_API', 'false')
  const coverage = mockChart('/api/heatmap', { from: '2026-01-05', to: '2026-01-05', tz: 'UTC' }).coverage
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status, mode: 'live', coverage, generation: 1, last_updated: updated, sync_lag_seconds: lag }))))
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><SyncStatus /></QueryClientProvider>)
}
it('shows unknown lag and no import without inventing zero', async () => {
  showStatus('degraded', null, null)
  expect(await screen.findByText(/Live data · Sync degraded · Lag: unknown · No successful import yet/)).toBeTruthy()
})
it('shows successful freshness and measured lag', async () => {
  showStatus('ok', 120, '2026-01-06T00:00:00Z')
  expect(await screen.findByText(/Sync OK · Lag: 2 min · Last import:/)).toBeTruthy()
})
