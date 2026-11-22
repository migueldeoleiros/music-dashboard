import { useQuery } from '@tanstack/react-query'
import { fetchHistoryJson, HistoryApiError, type ArtistCredit } from '../../api/history'

export const MAX_CREDIT_KEYS = 100
const NO_CREDITS: Record<string, ArtistCredit[]> = {}

// Transient limits (429) and server errors (5xx) must not drop links: retry twice with backoff.
export const retryCredits = (failures: number, error: unknown) =>
  failures < 2 && error instanceof HistoryApiError && (error.status === 429 || error.status >= 500)
export const creditsRetryDelay = (attempt: number) => Math.min(500 * 2 ** attempt, 4000)

/** Credits for a whole ranking list in ONE request. Maps key -> credits; missing key = unresolved (no link). */
export function useArtistCredits(dimension: 'albums' | 'tracks', keys: string[], active = true): Record<string, ArtistCredit[]> {
  const unique = [...new Set(keys)].slice(0, MAX_CREDIT_KEYS)
  const query = useQuery({
    queryKey: ['artist-credits', dimension, unique],
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ dimension })
      unique.forEach(key => params.append('key', key))
      return (await fetchHistoryJson<{ credits?: Record<string, ArtistCredit[]> }>(`/api/artist-credits?${params}`, signal)).credits ?? NO_CREDITS
    },
    retry: retryCredits,
    retryDelay: creditsRetryDelay,
    staleTime: 300_000,
    enabled: active && unique.length > 0 && import.meta.env.VITE_MOCK_API !== 'true',
  })
  return query.data ?? NO_CREDITS
}
