import { afterEach, expect, it } from 'vitest'
import { presetRange, readFilters, readPeriod, writeFilters } from '../src/features/dashboard/filters'

const now = new Date('2026-03-09T00:30:00Z')
const tomorrow = new Date('2026-03-10T00:30:00Z')
const tz = 'America/New_York'
afterEach(() => window.history.replaceState(null, '', '/'))

it('recalculates ?preset=today in the browser timezone on load/navigation, while explicit dates stay fixed', () => {
  const live = '?preset=today'
  expect(readFilters(live, tz, now)).toEqual({ from: '2026-03-08', to: '2026-03-08', tz })
  expect(readFilters(live, tz, tomorrow)).toEqual({ from: '2026-03-09', to: '2026-03-09', tz })
  for (const prefix of ['', 'preset=today&', 'preset=7d&']) {
    const fixed = `?${prefix}from=2026-03-08&to=2026-03-08`
    const filters = readFilters(fixed, tz, now)
    expect(readFilters(fixed, tz, tomorrow)).toEqual(filters)
    expect(readPeriod(fixed, filters)).toBe('custom')
  }
})

it('keeps even future explicit dates fixed rather than silently changing a valid pair', () => {
  const search = '?preset=today&from=2027-01-01&to=2027-01-02'
  expect(readFilters(search, tz, now)).toEqual({ from: '2027-01-01', to: '2027-01-02', tz })
  expect(readPeriod(search, readFilters(search, tz, now))).toBe('custom')
})

it.each([
  'from=invalid&to=2026-03-08', 'from=2026-02-30&to=2026-03-08',
  'from=2026-03-09&to=2026-03-08', 'from=2026-03-08', 'to=2026-03-08',
  'from=&to=', 'from=0000-01-01&to=2026-03-08',
])('falls back to 7d for invalid/incomplete dates, even with a valid preset: %s', pair => {
  for (const prefix of ['', 'preset=today&']) {
    const search = `?${prefix}${pair}`
    const filters = readFilters(search, tz, now)
    expect(filters).toEqual(presetRange('7d', tz, now))
    expect(readPeriod(search, filters)).toBe('7d')
  }
})

it('preserves unrelated repeated parameters, pathname and hash through preset/custom normalization', () => {
  window.history.replaceState(null, '', '/dashboard?tag=a&tag=b#rankings')
  const search = '?tag=a&tag=b&from=2026-03-08&to=2026-03-08&preset=week&tz=UTC'
  const filters = readFilters(search, tz, now)
  expect(writeFilters(filters, search, 'custom')).toBe('?tag=a&tag=b&from=2026-03-08&to=2026-03-08#rankings')
  const live = writeFilters(presetRange('today', tz, now), search, 'today')
  window.history.pushState(null, '', live)
  expect(window.location.pathname).toBe('/dashboard')
  expect(window.location.search).toBe('?tag=a&tag=b&preset=today')
  expect(window.location.hash).toBe('#rankings')
})
