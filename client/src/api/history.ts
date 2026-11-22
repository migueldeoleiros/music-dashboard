// Canonical DTOs: contracts/artist-history.md and schemas/history.py.
// Keep separate from frozen aggregate response types (no freshness or listen IDs).
export type ArtistCredit = { key: string | null; label: string }
export type PublicSong = { title: string; artists: ArtistCredit[]; album: string | null; image_url: string | null }
export type HistoryItem = PublicSong & { group: string }
export type FeedPage<T> = { items: T[]; next_cursor: string | null; generation: string }
export type HistoryData = FeedPage<HistoryItem> & { timezone: string }
export type ArtistSummary = { key: string; label: string; image_url: string | null; genres: string[]; total_listens: number }
export type ArtistRankedItem = { key: string; label: string; listens: number; image_url: string | null }
export type ArtistRankingPage = FeedPage<ArtistRankedItem> & { dimension: 'albums' | 'tracks' }
export type ArtistHeatmap = { year: number; years: number[]; timezone: string; week_starts_on: 'monday'; cells: { day: string; listens: number }[] }

export class HistoryApiError extends Error {
  constructor(public status: number) { super(status === 503 ? 'This view is currently unavailable.' : 'Could not load this view.') }
}

export async function fetchHistoryJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, cache: 'no-store', headers: { Accept: 'application/json' } })
  // Never render raw server error bodies on these public pages.
  if (!response.ok) throw new HistoryApiError(response.status)
  return await response.json() as T
}

export function artistApi(key: string): string { return `/api/artists/${encodeURIComponent(key)}` }
export function pageUrl(url: string, cursor: string | null): string {
  if (!cursor) return url
  return `${url}${url.includes('?') ? '&' : '?'}${new URLSearchParams({ cursor })}`
}
