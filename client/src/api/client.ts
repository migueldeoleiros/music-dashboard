import type { components, paths } from './schema'
import type { Filters } from '../features/dashboard/filters'

type Schemas = components['schemas']
export type CountryArtistsPath = '/api/countries/{code}/artists'
// Code-free, query-only chart routes. The path-param route has its own typed fetcher.
export type ChartPath = Exclude<keyof paths, '/api/health' | '/api/refresh' | '/api/summary' | CountryArtistsPath | '/api/history' | '/api/artist-credits' | `/api/artists/${string}`>
export const COUNTRY_ARTISTS_LIMIT = 50
export type ChartResponse<P extends ChartPath | CountryArtistsPath> = paths[P]['get']['responses'][200]['content']['application/json']
export type ChartData = Schemas['RankingResponse'] | Schemas['HeatmapResponse'] | Schemas['CountryResponse'] | Schemas['EvolutionResponse'] | Schemas['PieResponse']

export class ApiError extends Error {
  constructor(public status: number, public body: Schemas['ErrorResponse']) { super(body.message) }
  get timezoneRejected() { return this.status === 400 && /timezone|time zone|\btz\b/i.test([this.body.message, ...this.body.details].join(' ')) }
}

function isApiError(value: unknown): value is Schemas['ErrorResponse'] {
  return typeof value === 'object' && value !== null && 'code' in value && (value.code === 'invalid_request' || value.code === 'unavailable') && 'message' in value && typeof value.message === 'string' && 'details' in value && Array.isArray(value.details) && value.details.every(detail => typeof detail === 'string')
}

export async function fetchChart<P extends ChartPath>(path: P, filters: Filters, signal?: AbortSignal): Promise<ChartResponse<P>> {
  // Only rankings are capped. Pies and evolution select series by share (>= 1%) on the server, so no limit is sent.
  const limit = path.startsWith('/api/rankings/') ? 10 : undefined
  const query = new URLSearchParams(limit === undefined ? { ...filters } : { ...filters, limit: String(limit) })
  return fetchJson<ChartResponse<P>>(`${path}?${query}`, signal)
}

export async function fetchSummary(filters: Pick<Filters, 'from' | 'to' | 'tz'>, signal?: AbortSignal): Promise<Schemas['SummaryResponse']> {
  return fetchJson<Schemas['SummaryResponse']>(`/api/summary?${new URLSearchParams(filters)}`, signal)
}

export async function fetchCountryArtists(code: string, filters: Filters, signal?: AbortSignal): Promise<ChartResponse<CountryArtistsPath>> {
  const query = new URLSearchParams({ ...filters, limit: String(COUNTRY_ARTISTS_LIMIT) })
  return fetchJson<ChartResponse<CountryArtistsPath>>(`/api/countries/${encodeURIComponent(code)}/artists?${query}`, signal)
}

export async function fetchHealth(signal?: AbortSignal): Promise<Schemas['HealthResponse']> {
  return fetchJson<Schemas['HealthResponse']>('/api/health', signal)
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal, headers: { Accept: 'application/json' } })
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null)
    throw new ApiError(res.status, isApiError(body) ? body : { code: 'unavailable', message: `Statistics unavailable (HTTP ${res.status}). Please retry.`, details: [] })
  }
  return await res.json() as T
}
