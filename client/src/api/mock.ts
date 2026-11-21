import fixtures from './fixtures/aggregate-responses.json'
import type { components } from './schema'
import { ApiError, type ChartPath, type ChartResponse, type ChartData, type CountryArtistsPath } from './client'
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

// Mock country artists: relative weights only. Listens are scaled so that every country's
// artists sum to that country's /api/countries mock total (same reconciliation as the server).
type MockArtist = { key: string; label: string; weight: number }
const MOCK_COUNTRY_ARTISTS: Record<string, MockArtist[]> = {
  US: [
    { key: '0b6e9a4c-5f7a-4d3e-9a51-2f1c7d8e4a10', label: 'Blue Harbor', weight: 2 },
    { key: '7c1d2e3f-8a9b-4c5d-b6e7-0a1b2c3d4e5f', label: 'Aoi Tanaka', weight: 1 },
  ],
  JP: [{ key: '2d4f6a8c-1b3d-4f57-9a9b-3c5e7f90a1b2', label: 'Tokyo Tides', weight: 1 }],
}
const PSEUDO_COUNTRIES = new Set(['XW', 'XE', 'ZZ'])

export function mockCountryArtists(code: string, filters: Filters, scenario?: Scenario): ChartResponse<CountryArtistsPath> {
  if (!/^[A-Z]{2}$/.test(code) || PSEUDO_COUNTRIES.has(code)) {
    throw new ApiError(400, { code: 'invalid_request', message: 'Unsupported country code', details: [] })
  }
  const base = mockChart('/api/countries', filters, 10, scenario)
  const total = base.countries.find(country => country.code === code)?.listens ?? 0
  const artists = total > 0 ? MOCK_COUNTRY_ARTISTS[code] ?? [] : []
  const weights = artists.reduce((sum, artist) => sum + artist.weight, 0)
  const included = base.coverage.countries.included_listens
  const items = artists.map(artist => {
    const listens = total * artist.weight / weights
    return { key: artist.key, label: artist.label, mbid: artist.key, metadata_state: 'resolved' as const, listens, share: included ? listens / included : 0, image_url: null }
  })
  return { range: base.range, coverage: base.coverage, last_updated: base.last_updated, generation: base.generation, fixture: base.fixture, dimension: 'artists', items }
}

export type Coverage = components['schemas']['Coverage']
