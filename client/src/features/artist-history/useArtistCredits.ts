import { useQuery } from '@tanstack/react-query'
import { fetchHistoryJson, type ArtistCredit } from '../../api/history'

/** Reliable per-entity credits for albums/tracks. Empty (no links) on any failure or ambiguity. */
export function useArtistCredits(dimension: 'albums' | 'tracks', key: string, active = true): ArtistCredit[] {
  const query = useQuery({
    queryKey: ['artist-credits', dimension, key],
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ dimension, key })
      return (await fetchHistoryJson<{ artists: ArtistCredit[] }>(`/api/artist-credits?${params}`, signal)).artists ?? []
    },
    retry: false,
    staleTime: 300_000,
    enabled: active && import.meta.env.VITE_MOCK_API !== 'true',
  })
  return query.data ?? []
}
