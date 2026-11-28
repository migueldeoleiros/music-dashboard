import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useRef, useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mockChart, mockCountryArtists } from './support/mock'
import type { ChartPath } from '../src/api/client'
import { buildCountryOption, buildPieOption } from '../src/components/charts/builders'
import { readTheme, tooltipStyle } from '../src/components/charts/theme'
import { CountryArtistsPanel, SHEET_QUERY } from '../src/features/origin-map/CountryArtistsPanel'
import { App } from '../src/app/App'

vi.mock('../src/components/ui/SyncStatus', () => ({ SyncStatus: () => <p>Sync status</p> }))
// Charts are canvas; stand-ins expose the selection contract only.
vi.mock('../src/components/charts', () => ({
  stableColor: () => '#88ccaa',
  HeatmapChart: () => <p>Daily chart</p>, EvolutionChart: () => <p>Evolution chart</p>, PieChart: () => <p>Pie chart</p>,
  CountryChart: ({ selectedCountry, onCountrySelect }: { selectedCountry?: string | null; onCountrySelect?: (code: string | null) => void }) =>
    <button type="button" onClick={() => onCountrySelect?.(selectedCountry === 'US' ? null : 'US')}>pick US ({selectedCountry ?? 'none'})</button>,
}))

const range = { from: '2026-01-05', to: '2026-01-05', tz: 'UTC' }

function stubMedia(matching: string[]) {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: matching.includes(query), media: query, addEventListener() {}, removeEventListener() {} }))
}
let queryClient: QueryClient
beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const parsed = new URL(url, 'http://localhost')
    const [, , , code, tail] = parsed.pathname.split('/')
    const filters = { from: parsed.searchParams.get('from') ?? '', to: parsed.searchParams.get('to') ?? '', tz: parsed.searchParams.get('tz') ?? '' }
    const body = tail === 'artists' ? mockCountryArtists(code, filters) : mockChart(parsed.pathname as ChartPath, filters)
    return new Response(JSON.stringify(body), { status: 200 })
  }))
})
afterEach(() => { cleanup(); queryClient.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); document.body.style.overflow = ''; document.documentElement.style.overflow = '' })

function Harness({ onClosed }: { onClosed?: () => void }) {
  const [code, setCode] = useState<string | null>(null)
  const fallback = useRef<HTMLButtonElement>(null)
  return <QueryClientProvider client={queryClient}>
    <button type="button" onClick={() => setCode('US')}>open</button>
    <button type="button" ref={fallback}>fallback</button>
    <CountryArtistsPanel code={code} filters={range} onClose={() => { setCode(null); onClosed?.() }} restoreFocusRef={fallback} />
  </QueryClientProvider>
}

const openSheet = async () => {
  stubMedia([SHEET_QUERY])
  render(<Harness />)
  const opener = screen.getByText('open')
  opener.focus()
  fireEvent.click(opener)
  await screen.findByText('Blue Harbor')
  return opener
}

it('wide screens: side panel is complementary, no backdrop, no scroll lock', async () => {
  stubMedia([])
  render(<Harness />)
  fireEvent.click(screen.getByText('open'))
  await screen.findByText('Blue Harbor')
  expect(screen.getByRole('complementary')).toBeTruthy()
  expect(document.querySelector('.sheet-backdrop')).toBeNull()
  expect(document.body.style.overflow).toBe('')
})

it('mobile: opens as a modal dialog with backdrop, locks page scroll and focuses the sheet', async () => {
  await openSheet()
  const dialog = screen.getByRole('dialog', { name: 'Artists from United States' })
  expect(dialog.getAttribute('aria-modal')).toBe('true')
  expect(document.querySelector('.sheet-backdrop')).toBeTruthy()
  expect(document.body.style.overflow).toBe('hidden')
  expect(document.documentElement.style.overflow).toBe('hidden')
  expect(document.activeElement).toBe(dialog)
})

it('mobile: Escape closes, unlocks scroll and returns focus to the opener', async () => {
  const opener = await openSheet()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.body.style.overflow).toBe('')
  expect(document.activeElement).toBe(opener)
})

it('mobile: close button closes the sheet', async () => {
  await openSheet()
  fireEvent.click(screen.getByRole('button', { name: /Close United States/ }))
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('mobile: a backdrop click right after opening is the ghost click of the opening tap and is ignored', async () => {
  const now = vi.spyOn(performance, 'now')
  now.mockReturnValue(1000)
  await openSheet()
  now.mockReturnValue(1100)
  fireEvent.click(document.querySelector('.sheet-backdrop')!)
  expect(screen.getByRole('dialog')).toBeTruthy()
  now.mockReturnValue(2000)
  fireEvent.click(document.querySelector('.sheet-backdrop')!)
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('mobile: Tab and Shift+Tab stay inside the sheet', async () => {
  await openSheet()
  const dialog = screen.getByRole('dialog')
  const close = screen.getByRole('button', { name: /Close United States/ })
  const buttons = Array.from(dialog.querySelectorAll<HTMLElement>('button, a[href], [tabindex="0"]'))
  const last = buttons[buttons.length - 1]
  last.focus()
  fireEvent.keyDown(document, { key: 'Tab' })
  expect(document.activeElement).toBe(buttons[0])
  close.focus()
  fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
  expect(dialog.contains(document.activeElement)).toBe(true)
})

it('mobile: dragging the handle down past the threshold closes the sheet; a short drag does not', async () => {
  await openSheet()
  const handle = document.querySelector('.sheet-handle')!
  fireEvent.pointerDown(handle, { clientY: 100, pointerId: 1 })
  fireEvent.pointerUp(handle, { clientY: 130, pointerId: 1 })
  expect(screen.getByRole('dialog')).toBeTruthy()
  fireEvent.pointerDown(handle, { clientY: 100, pointerId: 1 })
  fireEvent.pointerUp(handle, { clientY: 200, pointerId: 1 })
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('focus falls back to the provided element when the opener was not focusable (canvas tap)', async () => {
  stubMedia([])
  render(<Harness />)
  ;(document.activeElement as HTMLElement | null)?.blur()
  fireEvent.click(screen.getByText('open'))
  await screen.findByText('Blue Harbor')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(document.activeElement).toBe(screen.getByText('fallback'))
})

it('App wiring: clicking a row in the country list opens that country artists', async () => {
  stubMedia([])
  window.history.replaceState(null, '', '?from=2026-01-05&to=2026-01-05')
  render(<QueryClientProvider client={queryClient}><App /></QueryClientProvider>)
  const ranking = await screen.findByRole('region', { name: 'Countries ranked by listens' })
  const [row] = await within(ranking).findAllByRole('button')
  fireEvent.click(row)
  expect(await screen.findByRole('complementary')).toBeTruthy()
  expect(screen.queryByRole('region', { name: 'Countries ranked by listens' })).toBeNull()
  expect(vi.mocked(fetch).mock.calls.some(([url]) => /\/api\/countries\/[A-Z]{2}\/artists/.test(String(url)))).toBe(true)
})

it('App wiring: map selection opens the panel beside the map and survives a period change', async () => {
  stubMedia([])
  window.history.replaceState(null, '', '?from=2026-01-05&to=2026-01-05')
  render(<QueryClientProvider client={queryClient}><App /></QueryClientProvider>)
  // No country selected: the panel beside the map ranks countries by listens (no images), nothing is an artists panel.
  const ranking = await screen.findByRole('region', { name: 'Countries ranked by listens' })
  expect(document.querySelector('.origins-layout')!.classList.contains('has-panel')).toBe(true)
  await waitFor(() => expect(within(ranking).getAllByRole('listitem').length).toBeGreaterThan(0))
  const rows = within(ranking).getAllByRole('listitem').map(li => li.textContent ?? '')
  expect(rows[0]).toMatch(/^01.+/)
  expect(ranking.querySelector('.entity-image, img')).toBeNull()
  const values = rows.map(text => Number(text.replace(/\D+$/, '').match(/(\d[\d,.]*)$/)?.[1].replace(/,/g, '')))
  expect([...values].sort((a, b) => b - a)).toEqual(values) // ranked by listens, descending
  expect(screen.queryByRole('complementary')).toBeNull()
  const pick = await screen.findByRole('button', { name: /pick US/ })
  fireEvent.click(pick)
  expect(await screen.findByRole('complementary', { name: 'Artists from United States' })).toBeTruthy()
  expect(screen.queryByRole('region', { name: 'Countries ranked by listens' })).toBeNull() // selection swaps ranking for artists
  expect(document.querySelector('.origins-layout')!.classList.contains('has-panel')).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '30 days' }))
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/api/countries/US/artists') && String(url).includes('to='))).toBe(true))
  expect(screen.getByRole('complementary')).toBeTruthy()
  const countryCalls = vi.mocked(fetch).mock.calls.map(([url]) => String(url)).filter(url => url.includes('/countries/US/artists'))
  expect(new Set(countryCalls).size).toBeGreaterThan(1)
  // The mock has no listens in this range, so the map is an empty state; the panel stays and shows its own empty state.
  expect(await screen.findByText(/No artists for this country/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Close United States/ }))
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(await screen.findByRole('region', { name: 'Countries ranked by listens' })).toBeTruthy() // close restores the ranking
})

it('charts: coarse pointers open tooltips on tap and get bigger marks; mouse keeps hover; reduced motion disables animation', () => {
  const base = readTheme()
  expect(tooltipStyle({ ...base, coarse: true }).triggerOn).toBe('click')
  expect(tooltipStyle({ ...base, coarse: false }).triggerOn).toBe('mousemove|click')
  expect(tooltipStyle({ ...base, coarse: true }).confine).toBe(true)
  const countries = { range, coverage: {}, countries: [{ code: 'SG', listens: 3, share: 1 }], partial_credit_note: '' } as never
  const coarse = buildCountryOption(countries, { ...base, coarse: true }) as { geo: { roam: unknown }; series: { data: { symbolSize: number }[] }[] }
  const fine = buildCountryOption(countries, { ...base, coarse: false }) as typeof coarse
  expect(coarse.geo.roam).toBe(true)
  expect(fine.geo.roam).toBe('move')
  expect(coarse.series[1].data[0].symbolSize).toBeGreaterThan(fine.series[1].data[0].symbolSize)
  const pie = { range, coverage: {}, dimension: 'artists', slices: [{ key: 'a', label: 'A', listens: 1, share: 1, is_other: false }] } as never
  expect((buildPieOption(pie, { ...base, reducedMotion: true }) as { animation: boolean }).animation).toBe(false)
  expect((buildPieOption(pie, { ...base, reducedMotion: false }) as { animation: boolean }).animation).toBe(true)
})
