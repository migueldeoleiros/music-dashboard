import { cleanup } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { mockChart } from './support/mock'
import { ApiError, type ChartPath } from '../src/api/client'

const filters = { from: '2026-01-05', to: '2026-01-05', tz: 'UTC' }
const paths: ChartPath[] = ['/api/rankings/artists', '/api/rankings/albums', '/api/rankings/tracks', '/api/rankings/genres', '/api/heatmap', '/api/countries', '/api/evolution/artists', '/api/evolution/genres', '/api/pies/artists', '/api/pies/genres']
afterEach(cleanup)
it('covers every endpoint with partial, full and empty fixtures', () => {
  for (const path of paths) {
    for (const scenario of ['partial', 'full', 'empty'] as const) {
      const data = mockChart(path, filters, 10, scenario)
      expect(data.fixture).toBe(true)
      expect(data.range).toEqual(filters)
      for (const dimension of Object.values(data.coverage)) expect(dimension.included_listens + dimension.excluded_listens).toBe(dimension.total_listens)
      expect(data.coverage.genres.included_listens).toBe(scenario === 'empty' ? 0 : scenario === 'full' ? 4 : 3)
    }
  }
})
it('keeps weighted totals equal to included listens in genre/country/pie fixtures', () => {
  for (const scenario of ['partial', 'full'] as const) {
    const genres = mockChart('/api/rankings/genres', filters, 10, scenario)
    expect(genres.items.reduce((sum, item) => sum + item.listens, 0)).toBe(genres.coverage.genres.included_listens)
    const countries = mockChart('/api/countries', filters, 10, scenario)
    expect(countries.countries.reduce((sum, item) => sum + item.listens, 0)).toBe(countries.coverage.countries.included_listens)
    const pie = mockChart('/api/pies/genres', filters, 10, scenario)
    expect(pie.slices.reduce((sum, item) => sum + item.listens, 0)).toBe(pie.coverage.genres.included_listens)
  }
})
it('returns a helpful typed timezone error and supports one-day / all-time ranges', () => {
  expect(() => mockChart('/api/heatmap', { ...filters, tz: 'Not/AZone' })).toThrow(ApiError)
  expect(mockChart('/api/heatmap', filters).cells).toHaveLength(1)
  expect(mockChart('/api/heatmap', { ...filters, from: '0001-01-01', to: '2026-11-21' }).cells).toHaveLength(1)
})
