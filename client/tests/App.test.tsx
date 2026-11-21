import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { App } from '../src/app/App'
import { mockChart } from '../src/api/mock'
import type { ChartPath } from '../src/api/client'
import { browserTimezone } from '../src/features/dashboard/filters'

vi.mock('../src/components/ui/SyncStatus', () => ({ SyncStatus: () => <p>Sync status</p> }))
vi.mock('../src/components/charts', () => ({
  stableColor: () => '#88ccaa',
  HeatmapChart: () => <p>Daily chart</p>, CountryChart: () => <p>Country chart</p>,
  EvolutionChart: () => <p>Evolution chart</p>, PieChart: () => <p>Pie chart</p>,
}))
let queryClient: QueryClient
beforeEach(() => {
  vi.setSystemTime(new Date('2026-11-21T12:00:00Z'))
  window.history.replaceState(null, '', '?from=2026-01-05&to=2026-01-05&tz=UTC')
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const parsed = new URL(url, 'http://localhost')
    const filters = { from: parsed.searchParams.get('from') ?? '', to: parsed.searchParams.get('to') ?? '', tz: parsed.searchParams.get('tz') ?? '' }
    return new Response(JSON.stringify(mockChart(parsed.pathname as ChartPath, filters)), { status: 200 })
  }))
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
})
afterEach(() => { cleanup(); queryClient.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })
function showApp() { return render(<QueryClientProvider client={queryClient}><App /></QueryClientProvider>) }

it('renders every view with dimension coverage and freshness, retaining missing metadata', async () => {
  showApp()
  expect(await screen.findByText('Blue Harbor')).toBeTruthy()
  expect(screen.getByText('Missing album')).toBeTruthy()
  expect(screen.getByText('Missing title')).toBeTruthy()
  expect(screen.getAllByText(/Genres available for 75%/)).toHaveLength(3)
  expect(screen.getByText(/Countries available for 50%/)).toBeTruthy()
  expect(screen.getAllByText(/Data freshness:/)).toHaveLength(10)
  expect(screen.getAllByText('Evolution chart')).toHaveLength(2)
  expect(screen.getAllByText('Pie chart')).toHaveLength(2)
  expect(screen.getByText('Daily chart')).toBeTruthy()
  expect(screen.getByText('Country chart')).toBeTruthy()
  expect(screen.queryByText(/^Unmapped$/)).toBeNull()
})

it('keeps every query consistent when selecting presets and all-time', async () => {
  showApp(); await screen.findByText('Blue Harbor')
  fireEvent.click(screen.getByRole('button', { name: 'All time' }))
  await waitFor(() => expect(new URLSearchParams(window.location.search).get('from')).toBe('0001-01-01'))
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBe(20))
  for (const [url] of vi.mocked(fetch).mock.calls.slice(-10)) {
    const params = new URL(String(url), 'http://localhost').searchParams
    expect(params.get('from')).toBe('0001-01-01'); expect(params.get('to')).toBe('2026-11-21'); expect(params.get('tz')).toBe('UTC')
  }
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-01-01' } })
  fireEvent.click(screen.getByRole('button', { name: 'All time' }))
  expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe('0001-01-01')
  fireEvent.click(screen.getByRole('button', { name: 'Today' }))
  expect(new URLSearchParams(window.location.search).get('from')).toBe('2026-11-21')
  expect(new URLSearchParams(window.location.search).get('to')).toBe('2026-11-21')
  await waitFor(() => expect(screen.getAllByText(/No listening data available/)).toHaveLength(10))
})

it('applies custom dates and timezone and resets to browser', async () => {
  showApp(); await screen.findByText('Blue Harbor')
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-01-01' } })
  fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-01-10' } })
  fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'Asia/Kathmandu' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply range' }))
  expect(new URLSearchParams(window.location.search).get('tz')).toBe('Asia/Kathmandu')
  expect(new URLSearchParams(window.location.search).get('from')).toBe('2026-01-01')
  fireEvent.click(screen.getByRole('button', { name: 'Reset to browser' }))
  expect(new URLSearchParams(window.location.search).get('tz')).toBe(browserTimezone())
})

it('reset to browser preserves a valid one-day range across a timezone midnight', async () => {
  vi.setSystemTime(new Date('2026-11-21T23:30:00Z'))
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'UTC' } as Intl.ResolvedDateTimeFormatOptions)
  window.history.replaceState(null, '', '?from=2026-11-22&to=2026-11-22&tz=Asia/Tokyo')
  showApp()
  fireEvent.click(screen.getByRole('button', { name: 'Reset to browser' }))
  const params = new URLSearchParams(window.location.search)
  expect(params.get('from')).toBe('2026-11-21'); expect(params.get('to')).toBe('2026-11-21'); expect(params.get('tz')).toBe('UTC')
  await waitFor(() => expect(screen.getAllByText(/No listening data available/)).toHaveLength(10))
  expect(screen.queryByRole('alert')).toBeNull()
})

it('handles browser back/popstate and rejects invalid URL dates without API calls', async () => {
  showApp(); await screen.findByText('Blue Harbor')
  vi.mocked(fetch).mockClear()
  window.history.replaceState(null, '', '?from=invalid&to=2026-01-05&tz=UTC')
  fireEvent.popState(window)
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(fetch).not.toHaveBeenCalled()
})

it('falls back to UTC visibly when typed 400 rejects timezone; all requests use fallback', async () => {
  window.history.replaceState(null, '', '?from=2026-01-05&to=2026-01-05&tz=Not/AZone')
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const parsed = new URL(url, 'http://localhost')
    if (parsed.searchParams.get('tz') !== 'UTC') return new Response(JSON.stringify({ code: 'invalid_request', message: 'Unsupported IANA timezone', details: ['Choose a supported tz'] }), { status: 400 })
    return new Response(JSON.stringify(mockChart(parsed.pathname as ChartPath, { from: '2026-01-05', to: '2026-01-05', tz: 'UTC' })))
  }))
  showApp()
  expect(await screen.findByText(/The API rejected the selected timezone/)).toBeTruthy()
  expect(await screen.findByText('Blue Harbor')).toBeTruthy()
  expect(new URLSearchParams(window.location.search).get('tz')).toBe('UTC')
  expect(screen.getByLabelText<HTMLInputElement>('Timezone').value).toBe('UTC')
})

it('shows typed non-timezone validation details and offers per-view retry', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'invalid_request', message: 'Range cannot be processed', details: ['Choose valid dates'] }), { status: 400 })))
  showApp()
  const region = screen.getByRole('region', { name: 'Top artists' })
  expect(await within(region).findByText('Choose valid dates')).toBeTruthy()
  expect(within(region).getByRole('button', { name: 'Retry this view' })).toBeTruthy()
  expect(new URLSearchParams(window.location.search).get('tz')).toBe('UTC')
})

it('shows loading and network errors per view', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('Network is offline'))))
  showApp()
  expect(screen.getAllByRole('status')).toHaveLength(10)
  await waitFor(() => expect(screen.getAllByText('Network is offline')).toHaveLength(10))
})
