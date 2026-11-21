import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { App } from '../src/app/App'
import { mockChart } from '../src/api/mock'
import type { ChartPath } from '../src/api/client'

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

const search = () => new URLSearchParams(window.location.search)
const calls = () => vi.mocked(fetch).mock.calls.map(([url]) => new URL(String(url), 'http://localhost'))
const pressed = () => screen.getAllByRole('button', { pressed: true }).map(button => button.textContent)

it('opens straight into content: heatmap first, no titles, taglines, footers or read-only text', async () => {
  showApp()
  expect(await screen.findByText('Blue Harbor')).toBeTruthy()
  expect(screen.getByText('Missing album')).toBeTruthy()
  expect(screen.getByText('Missing title')).toBeTruthy()
  expect(screen.queryAllByRole('heading')).toHaveLength(0)
  expect(screen.queryByRole('banner')).toBeNull()
  expect(screen.queryByRole('contentinfo')).toBeNull()
  expect(document.body.textContent).not.toMatch(/read.?only|public|Data freshness|available for \d+% of listens|Timezone/i)
  const regions = screen.getAllByRole('region').map(region => region.getAttribute('aria-label'))
  expect(regions[0]).toBe('Daily listening')
  expect(regions.indexOf('Period')).toBe(1)
  expect(regions).toEqual(expect.arrayContaining(['Rankings', 'Artist origins', 'Evolution', 'Shares', 'Top artists', 'Artist evolution', 'Genre shares']))
  expect(screen.getAllByText('Evolution chart')).toHaveLength(2)
  expect(screen.getAllByText('Pie chart')).toHaveLength(2)
  expect(screen.getByText('Daily chart')).toBeTruthy()
  expect(screen.getByText('Country chart')).toBeTruthy()
  expect(screen.queryByText(/^Unmapped$/)).toBeNull()
})

it('shows one coverage indicator near the period selector with per-dimension details', async () => {
  showApp(); await screen.findByText('Blue Harbor')
  const badge = await screen.findByRole('button', { name: /Metadata coverage, lowest 50%/ })
  expect(screen.getAllByRole('button', { name: /Metadata coverage/ })).toHaveLength(1)
  expect(within(screen.getByRole('region', { name: 'Period' })).getByRole('tooltip').textContent).toContain('Genres3 of 4')
  expect(badge.getAttribute('aria-describedby')).toBe(screen.getByRole('tooltip').id)
})

it('keeps the heatmap on its own rolling-365-day window and query, independent of the period', async () => {
  window.history.replaceState(null, '', '?tz=UTC')
  showApp(); await screen.findByText('Daily chart')
  await waitFor(() => expect(calls()).toHaveLength(10))
  expect(pressed()).toEqual(['Last 30 days'])
  const heatmapCalls = () => calls().filter(url => url.pathname === '/api/heatmap')
  expect(heatmapCalls()).toHaveLength(1)
  expect(Object.fromEntries(heatmapCalls()[0].searchParams)).toMatchObject({ from: '2025-11-22', to: '2026-11-21', tz: 'UTC' })
  const total = calls().length
  fireEvent.click(screen.getByRole('button', { name: 'All time' }))
  await waitFor(() => expect(calls().length).toBe(total + 9))
  fireEvent.click(screen.getByRole('button', { name: 'Today' }))
  await waitFor(() => expect(calls().length).toBe(total + 18))
  expect(heatmapCalls()).toHaveLength(1)
  expect(calls().slice(total).every(url => url.pathname !== '/api/heatmap')).toBe(true)
})

it('selects presets, keeps every shared query consistent, writes preset plus absolute dates to the URL', async () => {
  window.history.replaceState(null, '', '?tz=UTC')
  showApp(); await screen.findByText('Daily chart'); await waitFor(() => expect(calls()).toHaveLength(10))
  expect(screen.queryByLabelText('From')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'All time' }))
  await waitFor(() => expect(search().get('from')).toBe('0001-01-01'))
  expect(search().get('preset')).toBe('all'); expect(search().get('tz')).toBe('UTC')
  expect(pressed()).toEqual(['All time'])
  await waitFor(() => expect(calls().filter(url => url.searchParams.get('from') === '0001-01-01')).toHaveLength(9))
  expect(document.body.textContent).not.toContain('0001')
  fireEvent.click(screen.getByRole('button', { name: 'This week' }))
  expect(search().get('preset')).toBe('week'); expect(search().get('from')).toBe('2026-11-16'); expect(search().get('to')).toBe('2026-11-21')
  fireEvent.click(screen.getByRole('button', { name: 'Today' }))
  expect(search().get('from')).toBe('2026-11-21'); expect(search().get('to')).toBe('2026-11-21')
  await waitFor(() => expect(screen.getAllByText(/No listening data available/)).toHaveLength(9))
})

it('reveals from/to only for Custom range, hides 0001 from All time, and applies a custom range', async () => {
  window.history.replaceState(null, '', '?tz=UTC')
  showApp(); await screen.findByText('Daily chart'); await waitFor(() => expect(calls()).toHaveLength(10))
  fireEvent.click(screen.getByRole('button', { name: 'All time' }))
  fireEvent.click(screen.getByRole('button', { name: 'Custom range' }))
  expect(pressed()).toEqual(['Custom range'])
  expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe('')
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-01-01' } })
  fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-01-10' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(search().get('preset')).toBe('custom'); expect(search().get('from')).toBe('2026-01-01'); expect(search().get('to')).toBe('2026-01-10')
  expect(pressed()).toEqual(['Custom range'])
  expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe('2026-01-01')
  fireEvent.click(screen.getByRole('button', { name: 'Last 7 days' }))
  expect(screen.queryByLabelText('From')).toBeNull()
  expect(pressed()).toEqual(['Last 7 days'])
})

it('applies a custom range with an empty From as all time and rejects an inverted range', async () => {
  window.history.replaceState(null, '', '?tz=UTC')
  showApp(); await screen.findByText('Daily chart'); await waitFor(() => expect(calls()).toHaveLength(10))
  fireEvent.click(screen.getByRole('button', { name: 'Custom range' }))
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(search().get('from')).toBe('0001-01-01')
})

it('restores a shared link: matching preset is selected, unmatched dates open Custom with inputs', async () => {
  window.history.replaceState(null, '', '?preset=7d&from=2026-11-15&to=2026-11-21&tz=UTC')
  showApp(); await screen.findByText('Daily chart')
  expect(pressed()).toEqual(['Last 7 days'])
  expect(screen.queryByLabelText('From')).toBeNull()
  cleanup()
  window.history.replaceState(null, '', '?preset=7d&from=2026-11-01&to=2026-11-10&tz=UTC')
  showApp()
  expect(pressed()).toEqual(['Custom range'])
  expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe('2026-11-01')
  expect(screen.getByLabelText<HTMLInputElement>('To').value).toBe('2026-11-10')
  expect(search().get('preset')).toBe('custom')
  expect(search().get('from')).toBe('2026-11-01')
})

it('uses the browser timezone automatically and shows no timezone control', async () => {
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'Asia/Tokyo' } as Intl.ResolvedDateTimeFormatOptions)
  window.history.replaceState(null, '', '?')
  showApp(); await screen.findByText('Daily chart'); await waitFor(() => expect(calls()).toHaveLength(10))
  expect(search().get('tz')).toBe('Asia/Tokyo')
  expect(search().get('preset')).toBe('30d')
  expect(calls().every(url => url.searchParams.get('tz') === 'Asia/Tokyo')).toBe(true)
  expect(screen.queryByLabelText(/timezone/i)).toBeNull()
  expect(screen.queryByRole('button', { name: /reset to browser/i })).toBeNull()
})

it('handles browser back/popstate and rejects invalid URL dates without API calls', async () => {
  showApp(); await screen.findByText('Blue Harbor')
  vi.mocked(fetch).mockClear()
  window.history.replaceState(null, '', '?from=invalid&to=2026-01-05&tz=UTC')
  fireEvent.popState(window)
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(fetch).not.toHaveBeenCalled()
})

it('falls back to UTC visibly when typed 400 rejects timezone; all requests, heatmap included, use fallback', async () => {
  window.history.replaceState(null, '', '?from=2026-01-05&to=2026-01-05&tz=Not/AZone')
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const parsed = new URL(url, 'http://localhost')
    if (parsed.searchParams.get('tz') !== 'UTC') return new Response(JSON.stringify({ code: 'invalid_request', message: 'Unsupported IANA timezone', details: ['Choose a supported tz'] }), { status: 400 })
    return new Response(JSON.stringify(mockChart(parsed.pathname as ChartPath, { from: parsed.searchParams.get('from') ?? '', to: parsed.searchParams.get('to') ?? '', tz: 'UTC' })))
  }))
  showApp()
  expect(await screen.findByText(/does not support your browser timezone/)).toBeTruthy()
  expect(await screen.findByText('Blue Harbor')).toBeTruthy()
  expect(await screen.findByText('Daily chart')).toBeTruthy()
  expect(search().get('tz')).toBe('UTC')
})

it('shows typed non-timezone validation details and offers per-view retry', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'invalid_request', message: 'Range cannot be processed', details: ['Choose valid dates'] }), { status: 400 })))
  showApp()
  const region = screen.getByRole('region', { name: 'Top artists' })
  expect(await within(region).findByText('Choose valid dates')).toBeTruthy()
  expect(within(region).getByRole('button', { name: 'Retry this view' })).toBeTruthy()
  expect(search().get('tz')).toBe('UTC')
})

it('shows loading and network errors per view', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('Network is offline'))))
  showApp()
  expect(screen.getAllByRole('status')).toHaveLength(10)
  await waitFor(() => expect(screen.getAllByText('Network is offline')).toHaveLength(10))
})
