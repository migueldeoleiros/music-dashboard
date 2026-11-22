import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { HistoryApiError } from '../src/api/history'
import type { components } from '../src/api/schema'
import { creditsRetryDelay, retryCredits } from '../src/features/artist-history/useArtistCredits'
import { RankingList } from '../src/features/dashboard/RankingList'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks() })

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const artist = (n: number) => `1111111${n % 10}-0000-4000-8000-000000000000`

it('a 10-row list issues ONE credits request; every row with a resolvable primary artist is a link', async () => {
  vi.stubEnv('VITE_MOCK_API', 'false')
  const items = Array.from({ length: 10 }, (_, i) => ({ key: id(i), label: `Album ${i}`, mbid: id(i), metadata_state: 'resolved', listens: 10 - i, share: 0.1, image_url: null })) as components['schemas']['RankedEntity'][]
  // Rows 3 and 7 are unresolvable (ambiguous/empty credits); row 5 is a collaboration (primary links the row).
  const credits: Record<string, { key: string | null; label: string }[]> = {}
  items.forEach((item, i) => { credits[item.key] = i === 3 || i === 7 ? [] : [{ key: artist(i), label: `Artist ${i}` }] })
  credits[id(5)].push({ key: artist(9), label: 'Guest' })
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ artists: [], credits }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const data = { dimension: 'albums', items } as components['schemas']['RankingResponse']
  const { container } = render(<QueryClientProvider client={new QueryClient()}><RankingList data={data} /></QueryClientProvider>)

  await waitFor(() => expect(container.querySelectorAll('a.row-link')).toHaveLength(8))
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const url = new URL(String((fetchMock.mock.calls[0] as unknown[])[0]), 'http://localhost')
  expect(url.pathname).toBe('/api/artist-credits')
  expect(url.searchParams.get('dimension')).toBe('albums')
  expect(url.searchParams.getAll('key')).toEqual(items.map(item => item.key))
  for (const [i, item] of items.entries()) {
    const link = screen.queryByRole('link', { name: item.label })
    if (i === 3 || i === 7) expect(link).toBeNull()
    else expect(link?.getAttribute('href')).toBe(`/artists/${artist(i)}`)
  }
  expect(screen.getByRole('link', { name: 'Guest' }).getAttribute('href')).toBe(`/artists/${artist(9)}`)
})

it('credits query retries 429/5xx at most twice with backoff, never other errors', () => {
  expect(retryCredits(0, new HistoryApiError(429))).toBe(true)
  expect(retryCredits(1, new HistoryApiError(503))).toBe(true)
  expect(retryCredits(2, new HistoryApiError(429))).toBe(false)
  expect(retryCredits(0, new HistoryApiError(400))).toBe(false)
  expect(retryCredits(0, new Error('x'))).toBe(false)
  expect(creditsRetryDelay(1)).toBeGreaterThan(creditsRetryDelay(0))
})
