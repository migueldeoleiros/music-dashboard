import { useQuery } from '@tanstack/react-query'
import { fetchCountryArtists } from '../../api/client'
import type { Filters } from '../dashboard/filters'

export function useCountryArtists(code: string, filters: Filters) {
  return useQuery({
    queryKey: ['country-artists', code, filters],
    queryFn: ({ signal }) => fetchCountryArtists(code, filters, signal),
    retry: false,
  })
}
