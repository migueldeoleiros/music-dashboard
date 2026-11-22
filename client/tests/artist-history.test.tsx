import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ArtistLink } from '../src/features/artist-history/ArtistLinks'
import { ArtistPage } from '../src/features/artist-history/ArtistPage'
import { HistoryPage, adjacentGroups } from '../src/features/artist-history/HistoryPage'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const song = (title: string, group: string, key: string | null = null) => ({ title, group, artists: [{ key, label: 'A' }], album: null, image_url: null })
const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)

it('links only reliable artist keys', () => {
  const { container } = render(<><ArtistLink artist={{ key: 'name:["a"]', label: 'N' }} /><ArtistLink artist={{ key: null, label: 'Plain' }} /><ArtistLink artist={{ key: 'guess', label: 'G' }} /></>)
  expect(container.querySelectorAll('a')).toHaveLength(1)
  expect(container.querySelector('a')?.getAttribute('href')).toBe('/artists/' + encodeURIComponent('name:["a"]'))
})

it('merges adjacent equal groups across pages', () => {
  const items = [song('1', 'Today'), song('2', 'Today'), song('3', 'Last Week'), song('4', 'Today')]
  expect(adjacentGroups(items).map(g => [g.label, g.items.length])).toEqual([['Today', 2], ['Last Week', 1], ['Today', 1]])
})

it('history: groups pages, load more, replaces on 409, no timestamps rendered', async () => {
  const urls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url)
    if (url.includes('cursor=c1')) return json({ code: 'unavailable', message: 'x', details: [] }, 409)
    if (urls.filter(u => !u.includes('cursor')).length > 1) return json({ items: [song('Fresh', 'Today')], next_cursor: null, generation: 'g2', timezone: 'UTC' })
    return json({ items: [song('One', 'Today'), song('Two', 'Today')], next_cursor: 'c1', generation: 'g1', timezone: 'UTC' })
  }))
  render(<HistoryPage />)
  expect(await screen.findByRole('heading', { name: 'Today' })).toBeTruthy()
  expect(screen.getByText('One')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
  expect(await screen.findByText('Fresh')).toBeTruthy()
  expect(screen.queryByText('One')).toBeNull()
  expect(screen.getByRole('status', { name: '' }).textContent).toMatch(/reloaded/)
})

it('history: error retry and empty state', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({}, 503)))
  render(<HistoryPage />)
  expect(await screen.findByRole('alert')).toBeTruthy()
  vi.stubGlobal('fetch', vi.fn(async () => json({ items: [], next_cursor: null, generation: 'g', timezone: 'UTC' })))
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('No listening history yet.')).toBeTruthy()
})

it('artist page: not-found state and new layout', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({}, 404)))
  wrap(<ArtistPage artistKey="gone" />)
  expect(await screen.findByRole('heading', { name: 'Artist not found' })).toBeTruthy()
  cleanup()
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/heatmap')) return json({ year: 2025, years: [2025, 2024], timezone: 'UTC', week_starts_on: 'monday', cells: [{ day: '2025-01-02', listens: 3 }] })
    if (url.includes('/rankings/') || url.includes('/recent')) return json({ items: [], next_cursor: null, generation: 'g', dimension: 'albums' })
    return json({ key: 'name:x', label: 'Blue', image_url: null, genres: ['rock'], total_listens: 12 })
  }))
  wrap(<ArtistPage artistKey="name:x" />)
  expect(await screen.findByRole('heading', { name: 'Blue' })).toBeTruthy()
  expect(await screen.findByRole('heading', { name: 'Daily listening' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Top albums' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Recent songs' })).toBeTruthy()
  expect(screen.queryByLabelText('Year')).toBeNull()
  expect(screen.queryByText(/all-time/)).toBeNull()
  expect(screen.getByRole('link', { name: 'Home' })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Listening history' })).toBeTruthy()
})
