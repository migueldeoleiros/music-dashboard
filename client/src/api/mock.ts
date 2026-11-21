import fixtures from './fixtures/aggregate-responses.json'
import type { components } from './schema'
import { ApiError, type ChartPath, type ChartResponse, type ChartData } from './client'
import type { Filters } from '../features/dashboard/filters'
import { validateFilters } from '../features/dashboard/filters'

type Scenario = 'partial' | 'full' | 'empty'
// Foundation fixtures are illustrative aggregates, not an analytics engine.
// Only serialization aliases (from_date/to_date -> from/to) were normalized in the copy.
export function mockChart<P extends ChartPath>(path: P, filters: Filters, _limit = 10, scenario?: Scenario): ChartResponse<P> {
  void _limit
  try { new Intl.DateTimeFormat('en', { timeZone: filters.tz }) } catch {
    throw new ApiError(400, { code: 'invalid_request', message: 'Unsupported IANA timezone', details: ['tz must name a supported IANA timezone.'] })
  }
  const error = validateFilters(filters)
  if (error) throw new ApiError(400, { code: 'invalid_request', message: error, details: [] })
  const key = path.slice(5) as keyof typeof fixtures
  const data = structuredClone(fixtures[key]) as ChartData
  data.range = { ...filters }
  const selected = scenario ?? new URLSearchParams(window.location.search).get('fixture') ?? 'partial'
  const sampleInRange = filters.from <= '2026-01-05' && filters.to >= '2026-01-05'
  if (selected === 'empty' || !sampleInRange) {
    for (const dimension of Object.values(data.coverage)) {
      dimension.total_listens = 0; dimension.included_listens = 0; dimension.excluded_listens = 0
    }
    if ('items' in data) data.items = []
    if ('cells' in data) data.cells = []
    if ('countries' in data) data.countries = []
    if ('series' in data) { data.series = []; data.buckets = [] }
    if ('slices' in data) data.slices = []
  } else if (selected === 'full') {
    const scale = 'dimension' in data && data.dimension === 'genres' ? 4 / 3 : 'countries' in data ? 2 : 1
    if ('items' in data) for (const item of data.items) item.listens *= scale
    if ('countries' in data) for (const item of data.countries) item.listens *= scale
    if ('series' in data) for (const item of data.series) item.values = item.values.map(value => value * scale)
    if ('slices' in data) for (const item of data.slices) item.listens *= scale
    for (const dimension of Object.values(data.coverage)) { dimension.included_listens = 4; dimension.excluded_listens = 0 }
  }
  return data as ChartResponse<P>
}

export type Coverage = components['schemas']['Coverage']
