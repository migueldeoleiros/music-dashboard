import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { mockCountryArtists } from '../src/api/mock'
import type { components } from '../src/api/schema'
import { EntityImage } from '../src/components/ui/EntityImage'
import { hueIndex, initials, resolveImageSrc, sizedImageUrl } from '../src/components/ui/imageUrl'
import { RankingList } from '../src/features/dashboard/RankingList'
import { CountryArtistsPanel } from '../src/features/origin-map/CountryArtistsPanel'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks() })

const DZ = 'https://cdn-images.dzcdn.net/images/artist/abc123/1000x1000-000000-80-0-0.jpg'
it('sizedImageUrl picks allowed Deezer sizes and leaves other hosts alone', () => {
  expect(sizedImageUrl(DZ, 'sm')).toContain('/120x120-')
  expect(sizedImageUrl(DZ, 'sm', 2)).toContain('/250x250-')
  expect(sizedImageUrl(DZ, 'md')).toContain('/250x250-')
  expect(sizedImageUrl(DZ, 'lg')).toContain('/500x500-')
  expect(sizedImageUrl(DZ, 'hero')).toContain('/500x500-')
  expect(sizedImageUrl(DZ, 'hero', 2)).toContain('/1000x1000-')
  const caa = 'https://coverartarchive.org/release-group/x/front-250.jpg'
  expect(sizedImageUrl(caa, 'hero')).toBe(caa)
  const evil = 'https://evil.example/images/500x500-a.jpg'
  expect(sizedImageUrl(evil, 'sm')).toBe(evil)
  const noSegment = 'https://cdn-images.dzcdn.net/images/artist/abc/cover.jpg'
  expect(sizedImageUrl(noSegment, 'sm')).toBe(noSegment)
})

it('helpers: initials, hue, resolve', () => {
  expect(initials('Blue Harbor')).toBe('BH')
  expect(initials('  madonna ')).toBe('M')
  expect(initials('The Very Long Name')).toBe('TV')
  expect(initials('')).toBe('?')
  expect(hueIndex('Blue Harbor')).toBe(hueIndex('Blue Harbor'))
  expect(hueIndex('x')).toBeGreaterThanOrEqual(1)
  expect(resolveImageSrc('javascript:alert(1)', 'md')).toBeNull()
  expect(resolveImageSrc('//evil.example/a.png', 'md')).toBeNull()
  expect(resolveImageSrc('/local.png', 'md', { mock: true })).toBe('/local.png')
})

it('EntityImage renders lazy img and falls back to initials on error', () => {
  const { container } = render(<EntityImage src="https://example.com/a.png" label="Blue Harbor" size="md" />)
  const img = screen.getByAltText('Blue Harbor')
  expect(img.getAttribute('loading')).toBe('lazy')
  expect(img.getAttribute('decoding')).toBe('async')
  expect(img.getAttribute('referrerpolicy')).toBe('no-referrer')
  fireEvent.error(img)
  expect(container.querySelector('img')).toBeNull()
  expect(screen.getByRole('img', { name: 'Blue Harbor' }).textContent).toBe('BH')
})

it('EntityImage decorative image has empty alt; placeholder without src is hidden from AT', () => {
  const { container } = render(<><EntityImage src="https://example.com/a.png" label="A" size="sm" decorative /><EntityImage label="Zed Zed" size="sm" decorative /></>)
  expect(container.querySelector('img')?.getAttribute('alt')).toBe('')
  expect(container.querySelector('[aria-hidden="true"].entity-image--placeholder')?.textContent).toBe('ZZ')
})

it('mock mode never loads external URLs', () => {
  vi.stubEnv('VITE_MOCK_API', 'true')
  const { container } = render(<><EntityImage src={DZ} label="Blue Harbor" size="hero" /><EntityImage src="/art/local.png" label="Local" size="md" /></>)
  const srcs = [...container.querySelectorAll('img')].map(img => img.getAttribute('src'))
  expect(srcs).toEqual(['/art/local.png'])
  expect(screen.getByRole('img', { name: 'Blue Harbor' }).textContent).toBe('BH')
})

const range = { from: '2026-01-05', to: '2026-01-05' }
function ranking(dimension: 'artists' | 'genres', n = 3): components['schemas']['RankingResponse'] {
  const base = mockCountryArtists('US', { ...range, tz: 'UTC' })
  const items = Array.from({ length: n }, (_, i) => ({ ...base.items[0], key: `k${i}`, label: `Item ${i + 1}`, listens: 100 - i, share: 0.5 - i / 10, image_url: null }))
  return { ...base, dimension, items }
}

it('RankingList: hero first, rows after, no percentages', () => {
  const { container } = render(<RankingList data={ranking('artists')} />)
  const lis = container.querySelectorAll('ol > li')
  expect(lis).toHaveLength(3)
  expect(lis[0].className).toContain('entity-hero')
  expect(lis[0].textContent).toContain('Item 1')
  expect(lis[1].className).toContain('entity-row')
  expect(lis[1].querySelector('.entity-image--placeholder')).not.toBeNull()
  expect(container.textContent).not.toContain('%')
})

it('RankingList genres use text-only hero and rows', () => {
  const { container } = render(<RankingList data={ranking('genres')} />)
  expect(container.querySelector('.entity-image')).toBeNull()
  expect(container.querySelector('.entity-hero--text')).not.toBeNull()
  expect(container.textContent).not.toContain('%')
})

function Harness({ initial = 'US' }: { initial?: string | null }) {
  const [code, setCode] = useState<string | null>(null)
  return <><button onClick={() => setCode(initial)}>open</button><CountryArtistsPanel code={code} filters={{ ...range, tz: 'UTC' }} onClose={() => setCode(null)} /></>
}
function renderPanel(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}
function stubApi(handler: (url: string) => Response | Promise<Response>) { vi.stubGlobal('fetch', vi.fn(async (url: string) => handler(url))) }
const okFetch = (url: string) => {
  const code = new URL(url, 'http://x').pathname.split('/')[3]
  return new Response(JSON.stringify(mockCountryArtists(code, { ...range, tz: 'UTC' })), { status: 200 })
}

it('CountryArtistsPanel renders nothing without a code', () => {
  stubApi(okFetch)
  const { container } = renderPanel(<CountryArtistsPanel code={null} filters={{ ...range, tz: 'UTC' }} onClose={() => {}} />)
  expect(container.innerHTML).toBe('')
})

it('CountryArtistsPanel: loading, list, focus in, Escape closes and returns focus', async () => {
  stubApi(okFetch)
  renderPanel(<Harness />)
  const opener = screen.getByText('open')
  opener.focus()
  fireEvent.click(opener)
  const panel = screen.getByRole('complementary', { name: 'Artists from United States' })
  expect(screen.getByRole('status').textContent).toContain('Loading')
  expect(document.activeElement).toBe(panel)
  expect(await screen.findByText('Blue Harbor')).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'United States' })).toBeTruthy()
  expect(panel.textContent).toMatch(/listens/)
  expect(vi.mocked(fetch).mock.calls[0][0]).toContain('/api/countries/US/artists')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(document.activeElement).toBe(opener)
})

it('CountryArtistsPanel close button closes and returns focus', async () => {
  stubApi(okFetch)
  renderPanel(<Harness />)
  const opener = screen.getByText('open'); opener.focus(); fireEvent.click(opener)
  await screen.findByText('Blue Harbor')
  fireEvent.click(screen.getByRole('button', { name: /Close United States/ }))
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(document.activeElement).toBe(opener)
})

it('CountryArtistsPanel empty and error states', async () => {
  stubApi(okFetch)
  renderPanel(<Harness initial="FR" />)
  fireEvent.click(screen.getByText('open'))
  expect(await screen.findByText(/No artists for this country/)).toBeTruthy()
  cleanup()
  stubApi(() => new Response(JSON.stringify({ code: 'unavailable', message: 'down', details: [] }), { status: 503 }))
  renderPanel(<Harness />)
  fireEvent.click(screen.getByText('open'))
  expect(await screen.findByRole('alert')).toBeTruthy()
  stubApi(okFetch)
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(screen.getByText('Blue Harbor')).toBeTruthy())
})
