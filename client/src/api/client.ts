import type { components, paths } from './schema'
import type { Filters } from '../features/dashboard/filters'

type Schemas = components['schemas']
export type ChartPath = Exclude<keyof paths, '/api/health'>
export type ChartResponse<P extends ChartPath> = paths[P]['get']['responses'][200]['content']['application/json']
export type ChartData = Schemas['RankingResponse'] | Schemas['HeatmapResponse'] | Schemas['CountryResponse'] | Schemas['EvolutionResponse'] | Schemas['PieResponse']

export class ApiError extends Error {
  constructor(public status: number, public body: Schemas['ErrorResponse']) { super(body.message) }
  get timezoneRejected() { return this.status === 400 && /timezone|time zone|\btz\b/i.test([this.body.message, ...this.body.details].join(' ')) }
}

function isApiError(value: unknown): value is Schemas['ErrorResponse'] {
  return typeof value === 'object' && value !== null && 'code' in value && (value.code === 'invalid_request' || value.code === 'unavailable') && 'message' in value && typeof value.message === 'string' && 'details' in value && Array.isArray(value.details) && value.details.every(detail => typeof detail === 'string')
}

export async function fetchChart<P extends ChartPath>(path: P, filters: Filters, signal?: AbortSignal): Promise<ChartResponse<P>> {
  const limit = path.includes('/evolution/') ? 8 : 10
  if (import.meta.env.VITE_MOCK_API === 'true') {
    const { mockChart } = await import('./mock')
    return mockChart(path, filters, limit)
  }
  const query = new URLSearchParams({ ...filters, limit: String(limit) })
  return fetchJson<ChartResponse<P>>(`${path}?${query}`, signal)
}

export async function fetchHealth(signal?: AbortSignal): Promise<Schemas['HealthResponse']> {
  if (import.meta.env.VITE_MOCK_API === 'true') {
    const { mockChart } = await import('./mock')
    const sample = mockChart('/api/heatmap', { from: '2026-01-05', to: '2026-01-05', tz: 'UTC' })
    return { mode: 'fixture', status: 'ok', coverage: sample.coverage, generation: sample.generation, last_updated: sample.last_updated, sync_lag_seconds: null }
  }
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
