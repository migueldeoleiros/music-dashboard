import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { App } from '../src/app/App'
import { installDialogStub } from './dialog'
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
  installDialogStub()
  vi.setSystemTime(new Date('2026-11-21T12:00:00Z'))
  // The request timezone is always the browser's; pin it (individual tests override).
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'UTC' } as Intl.ResolvedDateTimeFormatOptions)
  window.history.replaceState(null, '', '?from=2026-01-05&to=2026-01-06')
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
const calls = () => vi.mocked(fetch).mock.calls.map(([url]) => new URL(String(url), 'http://localhost')).filter(url => url.pathname !== '/api/artist-credits')
const pressed = () => screen.getAllByRole('button', { pressed: true }).map(button => button.textContent)

it('opens straight into content: heatmap first, concise card titles only, no hero, taglines, footers or read-only text', async () => {
  showApp()
  expect(await screen.findByText('Blue Harbor')).toBeTruthy()
  expect(screen.getByText('Missing album')).toBeTruthy()
  expect(screen.getByText('Missing title')).toBeTruthy()
  // Titles sit right above (outside) their cards and name them; nothing else is a heading.
  const titles = screen.getAllByRole('heading', { level: 2 }).map(heading => heading.textContent)
  expect(titles).toEqual(['Daily listening', 'Top artists', 'Top albums', 'Top tracks', 'Top genres', 'Artist origins', 'Artist evolution', 'Genre evolution', 'Artist shares', 'Genre shares'])
  expect(screen.getAllByRole('heading').map(heading => heading.textContent)).toContain('Top countries')
  for (const heading of screen.getAllByRole('heading', { level: 2 })) {
    const card = heading.nextElementSibling
    expect(card?.tagName).toBe('SECTION')
    expect(card?.getAttribute('aria-labelledby')).toBe(heading.id)
    expect(card?.contains(heading)).toBe(false)
  }
  expect(document.querySelector('details, .chart-table')).toBeNull()
  expect(screen.queryByRole('banner')).toBeNull()
  expect(screen.queryByRole('contentinfo')).toBeNull()
  expect(document.body.textContent).not.toMatch(/read.?only|public|Data freshness|available for \d+% of listens|Timezone/i)
  const regions = screen.getAllByRole('region').map(region => region.getAttribute('aria-label') ?? document.getElementById(region.getAttribute('aria-labelledby') ?? '')?.textContent)
  expect(regions[0]).toBe('Daily listening')
  expect(regions.indexOf('Period')).toBe(1)
  expect(regions).toEqual(expect.arrayContaining(['Rankings', 'Artist origins', 'Evolution', 'Shares', 'Top artists', 'Artist evolution', 'Genre shares']))
  expect(screen.getAllByText('Evolution chart')).toHaveLength(2)
  expect(screen.getAllByText('Pie chart')).toHaveLength(2)
  expect(screen.getByText('Daily chart')).toBeTruthy()
  expect(screen.getByText('Country chart')).toBeTruthy()
  expect(screen.queryByText(/^Unmapped$/)).toBeNull()
})

it('does not show a metadata coverage indicator', async () => {
  showApp(); await screen.findByText('Blue Harbor')
  expect(screen.queryByRole('button', { name: /Metadata coverage/ })).toBeNull()
  expect(screen.queryByRole('tooltip')).toBeNull()
})

it('keeps the heatmap on its own rolling-365-day window and query, independent of the period', async () => {
  window.history.replaceState(null, '', '?')
  showApp(); await screen.findByText('Daily chart')
  await waitFor(() => expect(calls()).toHaveLength(10))
  expect(pressed()).toEqual(['7 days'])
  const heatmapCalls = () => calls().filter(url => url.pathname === '/api/heatmap')
  expect(heatmapCalls()).toHaveLength(1)
  expect(Object.fromEntries(heatmapCalls()[0].searchParams)).toMatchObject({ from: '2025-11-22', to: '2026-11-21', tz: 'UTC' })
  const total = calls().length
  fireEvent.click(screen.getByRole('button', { name: 'All time' }))
  await waitFor(() => expect(calls().length).toBe(total + 9))
  fireEvent.click(screen.getByRole('button', { name: 'Today' }))
  await waitFor(() => expect(calls().length).toBe(total + 9 + 7)) // a single day hides (and does not fetch) both evolution charts
  expect(heatmapCalls()).toHaveLength(1)
  expect(calls().slice(total).every(url => url.pathname !== '/api/heatmap')).toBe(true)
})

it('selects live presets, keeps shared queries consistent, and persists preset intent without dates or tz', async () => {
  window.history.replaceState(null, '', '?')
  showApp(); await screen.findByText('Daily chart'); await waitFor(() => expect(calls()).toHaveLength(10))
  expect(screen.queryByRole('dialog')).toBeNull()
  const period = screen.getByRole('region', { name: 'Period' })
  expect([...period.querySelectorAll('button[aria-pressed]')].map(button => button.textContent)).toEqual(['Today', '7 days', '30 days', 'This week', 'This month', 'This year', 'All time', 'Custom'])
  fireEvent.click(screen.getByRole('button', { name: 'All time' }))
  await waitFor(() => expect(pressed()).toEqual(['All time']))
  expect(window.location.search).toBe('?preset=all')
  await waitFor(() => expect(calls().filter(url => url.searchParams.get('from') === '0001-01-01')).toHaveLength(9))
  expect(document.body.textContent).not.toContain('0001')
  fireEvent.click(screen.getByRole('button', { name: 'This week' }))
  expect(window.location.search).toBe('?preset=week')
  expect(calls().some(url => url.searchParams.get('from') === '2026-11-16' && url.searchParams.get('to') === '2026-11-21' && url.searchParams.get('tz') === 'UTC')).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Today' }))
  expect(window.location.search).toBe('?preset=today')
  await waitFor(() => expect(screen.getAllByText(/No listening data available/)).toHaveLength(7))
})

it('hides both evolution charts for a single calendar day (Today or a one-day custom range) and shows them for multiple days', async () => {
  showApp(); await screen.findByText('Daily chart'); await waitFor(() => expect(calls()).toHaveLength(10))
  await waitFor(() => expect(screen.getAllByText('Evolution chart')).toHaveLength(2))
  expect(screen.getByRole('heading', { name: 'Artist evolution' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Today' }))
  expect(screen.queryByRole('heading', { name: /evolution/i })).toBeNull()
  expect(screen.queryByRole('region', { name: 'Evolution' })).toBeNull()
  expect(screen.queryByText('Evolution chart')).toBeNull()
  await waitFor(() => expect(calls().filter(url => url.pathname.startsWith('/api/evolution/') && url.searchParams.get('from') === '2026-11-21')).toHaveLength(0))
  fireEvent.click(screen.getByRole('button', { name: '7 days' }))
  expect(screen.getByRole('heading', { name: 'Genre evolution' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-01-05' } })
  fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-01-05' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(window.location.search).toBe('?from=2026-01-05&to=2026-01-05')
  expect(screen.queryByRole('heading', { name: /evolution/i })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-01-06' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  await waitFor(() => expect(screen.getAllByText('Evolution chart')).toHaveLength(2))
})

it('reveals from/to only for Custom, hides 0001 from All time, and applies a custom range', async () => {
  window.history.replaceState(null, '', '?')
  showApp(); await screen.findByText('Daily chart'); await waitFor(() => expect(calls()).toHaveLength(10))
  fireEvent.click(screen.getByRole('button', { name: 'All time' }))
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  expect(pressed()).toEqual(['All time']) // draft does not change the applied period
  expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe('')
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-01-01' } })
  fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-01-10' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(window.location.search).toBe('?from=2026-01-01&to=2026-01-10')
  expect(pressed()).toEqual(['Custom'])
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '7 days' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(pressed()).toEqual(['7 days'])
  expect(window.location.search).toBe('')
})

it('reloads a custom range from the URL, then switching back to a preset clears the range but keeps unrelated params', async () => {
  window.history.replaceState(null, '', '?fixture=partial&from=2026-01-01&to=2026-01-10')
  showApp(); await screen.findByText('Daily chart')
  expect(pressed()).toEqual(['Custom'])
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe('2026-01-01')
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(window.location.search).toBe('?fixture=partial&from=2026-01-01&to=2026-01-10')
  cleanup()
  showApp(); await screen.findByText('Daily chart')
  expect(pressed()).toEqual(['Custom'])
  fireEvent.click(screen.getByRole('button', { name: '7 days' }))
  expect(window.location.search).toBe('?fixture=partial')
  expect(pressed()).toEqual(['7 days'])
  cleanup()
  showApp(); await screen.findByText('Daily chart')
  expect(pressed()).toEqual(['7 days']) // reload of a preset is the bare default
})

it('reloading the bare page defaults to 7 days', async () => {
  window.history.replaceState(null, '', '/')
  showApp(); await screen.findByText('Daily chart')
  expect(pressed()).toEqual(['7 days'])
  expect(window.location.search).toBe('')
})

it('applies a custom range with an empty From as all time and rejects an inverted range', async () => {
  window.history.replaceState(null, '', '?')
  showApp(); await screen.findByText('Daily chart'); await waitFor(() => expect(calls()).toHaveLength(10))
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(search().get('from')).toBe('0001-01-01')
})

it('keeps explicit legacy mixed links fixed Custom even when dates match a preset', async () => {
  window.history.replaceState(null, '', '?preset=7d&from=2026-11-15&to=2026-11-21&tz=UTC')
  showApp(); await screen.findByText('Daily chart')
  expect(pressed()).toEqual(['Custom'])
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(window.location.search).toBe('?from=2026-11-15&to=2026-11-21')
  cleanup()
  window.history.replaceState(null, '', '?preset=7d&from=2026-11-01&to=2026-11-10&tz=UTC')
  showApp()
  expect(pressed()).toEqual(['Custom'])
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe('2026-11-01')
  expect(screen.getByLabelText<HTMLInputElement>('To').value).toBe('2026-11-10')
  expect(window.location.search).toBe('?from=2026-11-01&to=2026-11-10')
})

it('uses the browser timezone automatically and shows no timezone control', async () => {
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'Asia/Tokyo' } as Intl.ResolvedDateTimeFormatOptions)
  window.history.replaceState(null, '', '?')
  showApp(); await screen.findByText('Daily chart'); await waitFor(() => expect(calls()).toHaveLength(10))
  expect(window.location.search).toBe('')
  expect(calls().every(url => url.searchParams.get('tz') === 'Asia/Tokyo')).toBe(true)
  expect(screen.queryByLabelText(/timezone/i)).toBeNull()
  expect(screen.queryByRole('button', { name: /reset to browser/i })).toBeNull()
})

it('handles popstate and safely falls back from invalid mixed dates', async () => {
  showApp(); await screen.findByText('Blue Harbor')
  vi.mocked(fetch).mockClear()
  window.history.replaceState(null, '', '?preset=today&from=invalid&to=2026-01-05&fixture=full#rankings')
  fireEvent.popState(window)
  expect(pressed()).toEqual(['7 days'])
  expect(screen.queryByRole('alert')).toBeNull()
  await waitFor(() => expect(calls().some(url => url.searchParams.get('from') === '2026-11-15' && url.searchParams.get('to') === '2026-11-21')).toBe(true))
  expect(window.location.hash).toBe('#rankings')
})

it('back and forward restore live intent with fresh dates, while custom dates remain fixed', async () => {
  window.history.replaceState(null, '', '/?fixture=partial#rankings')
  showApp(); await screen.findByText('Daily chart')
  fireEvent.click(screen.getByRole('button', { name: 'Today' }))
  expect(window.location.search).toBe('?fixture=partial&preset=today')
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-01-05' } })
  fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-01-06' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(pressed()).toEqual(['Custom'])
  vi.setSystemTime(new Date('2026-11-22T12:00:00Z'))
  window.history.back()
  await waitFor(() => expect(pressed()).toEqual(['Today']))
  await waitFor(() => expect(calls().some(url => url.pathname !== '/api/heatmap' && url.searchParams.get('from') === '2026-11-22' && url.searchParams.get('to') === '2026-11-22')).toBe(true))
  expect(window.location.hash).toBe('#rankings')
  window.history.forward()
  await waitFor(() => expect(pressed()).toEqual(['Custom']))
  expect(window.location.search).toBe('?fixture=partial&from=2026-01-05&to=2026-01-06')
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }))
  expect(screen.getByLabelText<HTMLInputElement>('From').value).toBe('2026-01-05')
  expect(screen.getByLabelText<HTMLInputElement>('To').value).toBe('2026-01-06')
})

it('reloads a live Today URL rather than defaulting to 7d', async () => {
  window.history.replaceState(null, '', '/?preset=today&fixture=partial#rankings')
  showApp(); await screen.findByText('Daily chart')
  expect(pressed()).toEqual(['Today'])
  cleanup()
  vi.setSystemTime(new Date('2026-11-22T12:00:00Z'))
  showApp(); await screen.findByText('Daily chart')
  expect(pressed()).toEqual(['Today'])
  expect(window.location.search).toBe('?fixture=partial&preset=today')
  await waitFor(() => expect(calls().some(url => url.pathname !== '/api/heatmap' && url.searchParams.get('from') === '2026-11-22' && url.searchParams.get('to') === '2026-11-22')).toBe(true))
})

it('falls back to UTC visibly when typed 400 rejects timezone; all requests, heatmap included, use fallback', async () => {
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'Not/AZone' } as Intl.ResolvedDateTimeFormatOptions)
  window.history.replaceState(null, '', '?from=2026-01-05&to=2026-01-05')
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const parsed = new URL(url, 'http://localhost')
    if (parsed.searchParams.get('tz') !== 'UTC') return new Response(JSON.stringify({ code: 'invalid_request', message: 'Unsupported IANA timezone', details: ['Choose a supported tz'] }), { status: 400 })
    return new Response(JSON.stringify(mockChart(parsed.pathname as ChartPath, { from: parsed.searchParams.get('from') ?? '', to: parsed.searchParams.get('to') ?? '', tz: 'UTC' })))
  }))
  showApp()
  expect(await screen.findByText(/does not support your browser timezone/)).toBeTruthy()
  expect(await screen.findByText('Blue Harbor')).toBeTruthy()
  expect(await screen.findByText('Daily chart')).toBeTruthy()
  expect(search().has('tz')).toBe(false)
})

it('keeps an explicit tomorrow range fixed when browser timezone rejection falls back to UTC', async () => {
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'Not/AZone' } as Intl.ResolvedDateTimeFormatOptions)
  window.history.replaceState(null, '', '?from=2026-11-22&to=2026-11-22')
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const parsed = new URL(url, 'http://localhost')
    if (parsed.searchParams.get('tz') !== 'UTC') return new Response(JSON.stringify({ code: 'invalid_request', message: 'Unsupported IANA timezone', details: ['Choose a supported tz'] }), { status: 400 })
    return new Response(JSON.stringify(mockChart(parsed.pathname as ChartPath, { from: parsed.searchParams.get('from') ?? '', to: parsed.searchParams.get('to') ?? '', tz: 'UTC' })))
  }))
  showApp()
  expect(await screen.findByText(/does not support your browser timezone/)).toBeTruthy()
  expect(pressed()).toEqual(['Custom'])
  expect(window.location.search).toBe('?from=2026-11-22&to=2026-11-22')
  await waitFor(() => expect(calls().some(url => url.pathname !== '/api/heatmap' && url.searchParams.get('tz') === 'UTC' && url.searchParams.get('from') === '2026-11-22' && url.searchParams.get('to') === '2026-11-22')).toBe(true))
})

it('shows typed non-timezone validation details and offers per-view retry', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'invalid_request', message: 'Range cannot be processed', details: ['Choose valid dates'] }), { status: 400 })))
  showApp()
  const region = screen.getByRole('region', { name: 'Top artists' })
  expect(await within(region).findByText('Choose valid dates')).toBeTruthy()
  expect(within(region).getByRole('button', { name: 'Retry this view' })).toBeTruthy()
  expect(search().has('tz')).toBe(false)
})

it('shows loading and network errors per view', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('Network is offline'))))
  showApp()
  expect(screen.getAllByRole('status')).toHaveLength(11) // nine views, the country ranking beside the map, and the period/sync line
  await waitFor(() => expect(screen.getAllByText('Network is offline')).toHaveLength(10))
})
