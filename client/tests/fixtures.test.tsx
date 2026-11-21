import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { CoverageNote } from '../src/components/ui/CoverageNote'
import { mockChart } from '../src/api/mock'
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
it('summarizes per-dimension included of total listens in one indicator, including zero and 100%', () => {
  const full = mockChart('/api/pies/genres', filters, 10, 'full')
  const view = render(<CoverageNote coverage={full.coverage} />)
  expect(screen.getByRole('button', { name: 'Metadata coverage, lowest 100%' })).toBeTruthy()
  expect(screen.getByRole('tooltip').textContent).toContain('Genres4 of 4')
  view.rerender(<CoverageNote coverage={mockChart('/api/heatmap', filters, 10, 'empty').coverage} />)
  expect(screen.getByRole('button', { name: 'Metadata coverage, lowest 0%' })).toBeTruthy()
  view.rerender(<CoverageNote coverage={mockChart('/api/heatmap', filters, 10, 'partial').coverage} />)
  const popover = screen.getByRole('tooltip').textContent ?? ''
  expect(popover).toContain('Genres3 of 4')
  expect(popover).toContain('Countries2 of 4')
  expect(screen.getByRole('button').getAttribute('aria-describedby')).toBe(screen.getByRole('tooltip').id)
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
