import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { components } from '../src/api/schema'
// @ts-expect-error node types are not part of the client tsconfig; vitest runs in node
import { readFileSync } from 'node:fs'
import { SongRow } from '../src/components/ui/SongRow'
import { RankingList } from '../src/features/dashboard/RankingList'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })

const A = '11111111-0000-4000-8000-000000000000'
const B = '22222222-0000-4000-8000-000000000000'
const song = (artists: { key: string; label: string }[]) => ({ title: 'Song', group: 'g', artists, album: 'Alb', image_url: null })

it('SongRow on own artist page: no self link, feat link kept', () => {
  const { container } = render(<SongRow song={song([{ key: A, label: 'Main' }, { key: B, label: 'Feat' }])} excludeArtistKey={A} />)
  expect(container.querySelector('a.row-link')).toBeNull()
  expect(container.querySelector('li')?.className).not.toContain('row--link')
  expect(container.querySelector('.song-artist')?.firstChild?.textContent).toBe('Main')
  expect(container.querySelectorAll('.song-artist a')).toHaveLength(1)
  expect(screen.getByRole('link', { name: 'Feat' }).getAttribute('href')).toBe(`/artists/${B}`)
})

it('SongRow on a feat artist page: primary stays linked, own credit plain', () => {
  const { container } = render(<SongRow song={song([{ key: A, label: 'Main' }, { key: B, label: 'Feat' }])} excludeArtistKey={B} />)
  expect(container.querySelector('a.row-link')?.getAttribute('href')).toBe(`/artists/${A}`)
  expect(screen.queryByRole('link', { name: 'Feat' })).toBeNull()
})

it('SongRow without exclusion (History) unchanged', () => {
  const { container } = render(<SongRow song={song([{ key: A, label: 'Main' }, { key: B, label: 'Feat' }])} />)
  expect(container.querySelector('a.row-link')?.getAttribute('href')).toBe(`/artists/${A}`)
  expect(screen.getByRole('link', { name: 'Feat' })).toBeTruthy()
})

it('hover rule covers all song columns inside (hover: hover)', () => {
  const css: string = readFileSync('src/styles/ui.css', 'utf8')
  expect(css).toMatch(/@media \(hover: hover\) \{[^}]*\.row--link:hover \.song-cols > span/)
})

async function ranking(excludeArtistKey?: string) {
  const items = [0, 1].map(i => ({ key: `00000000-0000-4000-8000-00000000000${i}`, label: `Album ${i}`, mbid: null, metadata_state: 'resolved', listens: 5 - i, share: 0.1, image_url: null })) as components['schemas']['RankedEntity'][]
  const credits = { [items[0].key]: [{ key: A, label: 'Main' }, { key: B, label: 'Feat' }], [items[1].key]: [{ key: B, label: 'Other' }, { key: A, label: 'Guest' }] }
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ artists: [], credits }), { status: 200 })))
  const data = { dimension: 'albums', items } as components['schemas']['RankingResponse']
  return render(<QueryClientProvider client={new QueryClient()}><RankingList data={data} excludeArtistKey={excludeArtistKey} /></QueryClientProvider>)
}

it('RankingList on artist A page: A never linked; row 1 loses row link, row 2 keeps B link', async () => {
  const { container } = await ranking(A)
  await waitFor(() => expect(screen.getByRole('link', { name: 'Feat' })).toBeTruthy())
  const hrefs = [...container.querySelectorAll('a')].map(a => a.getAttribute('href'))
  expect(hrefs).not.toContain(`/artists/${A}`)
  expect(hrefs.filter(h => h === `/artists/${B}`)).toHaveLength(2) // Feat inline + row 2 primary link
  expect(screen.queryByRole('link', { name: 'Guest' })).toBeNull()
})

it('RankingList without exclusion links the primary and others', async () => {
  const { container } = await ranking()
  await waitFor(() => expect(container.querySelectorAll('a.row-link')).toHaveLength(2))
  expect(screen.getByRole('link', { name: 'Guest' })).toBeTruthy()
})
