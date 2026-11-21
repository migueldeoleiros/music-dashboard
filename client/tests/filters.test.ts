import { describe, expect, it } from 'vitest'
import { addDays, clampToToday, detectPeriod, presetRange, readFilters, readPeriod, rollingYearRange, todayInZone, utcFallback, validDate, validateFilters, weekStart, writeFilters } from '../src/features/dashboard/filters'

const now = new Date('2026-03-09T00:30:00Z')
describe('absolute local-date shared filters', () => {
  it('uses browser timezone by default and computes dates in that timezone', () => {
    expect(readFilters('', 'America/New_York', now)).toEqual({ from: '2026-02-07', to: '2026-03-08', tz: 'America/New_York' })
    expect(todayInZone('Asia/Kathmandu', now)).toBe('2026-03-09')
  })
  it('preserves explicit timezone and one-day ranges', () => {
    expect(readFilters('?from=2026-01-05&to=2026-01-05&tz=UTC', 'Asia/Tokyo', now)).toEqual({ from: '2026-01-05', to: '2026-01-05', tz: 'UTC' })
  })
  it('clamps future to to today in the selected timezone', () => {
    expect(clampToToday({ from: '2026-01-01', to: '2027-01-01', tz: 'America/New_York' }, now).to).toBe('2026-03-08')
  })
  it('presets write absolute calendar dates across DST, including all time', () => {
    expect(presetRange('7d', 'America/New_York', now)).toEqual({ from: '2026-03-02', to: '2026-03-08', tz: 'America/New_York' })
    expect(presetRange('all', 'UTC', now).from).toBe('0001-01-01')
    expect(presetRange('today', 'UTC', now).from).toBe('2026-03-09')
    expect(presetRange('year', 'UTC', now).from).toBe('2026-01-01')
    expect(addDays('2024-03-01', -1)).toBe('2024-02-29')
  })
  it('validates calendar dates, ordering, minimum year and empty timezone', () => {
    for (const date of ['2026-02-29', '2026-13-01', '0000-01-01', '2026-1-01', 'wrong']) expect(validDate(date)).toBe(false)
    expect(validDate('0001-01-01')).toBe(true)
    expect(validDate('2024-02-29')).toBe(true)
    expect(validateFilters({ from: '2026-01-06', to: '2026-01-05', tz: 'UTC' })).toMatch(/on or before/)
    expect(validateFilters({ from: '2026-01-05', to: '2026-01-05', tz: '' })).toMatch(/IANA/)
  })
  it('keeps fallback ranges valid when a rejected timezone is already tomorrow in UTC', () => {
    const midnight = new Date('2026-03-08T23:30:00Z')
    const next = utcFallback({ from: '2026-03-09', to: '2026-03-09', tz: 'Pacific/Apia' }, midnight)
    expect(next).toEqual({ from: '2026-03-08', to: '2026-03-08', tz: 'UTC' })
    expect(validateFilters(next)).toBeNull()
  })
  it('preserves unknown zones for API validation, and serializes links losslessly', () => {
    const filters = readFilters('?from=2026-01-05&to=2026-01-05&tz=Unknown/Zone', 'UTC', now)
    expect(filters.tz).toBe('Unknown/Zone')
    const search = writeFilters(filters, '?fixture=partial')
    expect(new URLSearchParams(search).get('fixture')).toBe('partial')
    expect(readFilters(search, 'UTC', now)).toEqual(filters)
  })
})

describe('tz-aware presets', () => {
  const at = (iso: string, tz: string, preset: Parameters<typeof presetRange>[0]) => presetRange(preset, tz, new Date(iso))
  it('starts the week on Monday, including Sunday and Monday themselves', () => {
    expect(weekStart('2026-11-23')).toBe('2026-11-23') // Monday
    expect(weekStart('2026-11-22')).toBe('2026-11-16') // Sunday belongs to the previous Monday
    expect(weekStart('2026-11-21')).toBe('2026-11-16') // Saturday
    expect(weekStart('2026-03-01')).toBe('2026-02-23') // crosses a month
    expect(weekStart('2025-01-01')).toBe('2024-12-30') // crosses a year
    expect(at('2026-11-22T12:00:00Z', 'UTC', 'week')).toEqual({ from: '2026-11-16', to: '2026-11-22', tz: 'UTC' })
    expect(at('2026-11-23T00:00:00Z', 'UTC', 'week')).toEqual({ from: '2026-11-23', to: '2026-11-23', tz: 'UTC' })
  })
  it('uses the visitor date, not UTC, at a day boundary', () => {
    const iso = '2026-03-09T00:30:00Z' // Mon in UTC/Madrid/Tokyo, still Sun in New York (DST started that morning)
    expect(at(iso, 'America/New_York', 'week')).toEqual({ from: '2026-03-02', to: '2026-03-08', tz: 'America/New_York' })
    expect(at(iso, 'Europe/Madrid', 'week')).toEqual({ from: '2026-03-09', to: '2026-03-09', tz: 'Europe/Madrid' })
    expect(at(iso, 'Asia/Tokyo', 'today').to).toBe('2026-03-09')
    expect(at('2026-06-30T23:30:00Z', 'UTC', 'month').from).toBe('2026-06-01')
    expect(at('2026-06-30T23:30:00Z', 'Asia/Tokyo', 'month')).toEqual({ from: '2026-07-01', to: '2026-07-01', tz: 'Asia/Tokyo' })
  })
  it('keeps calendar-day arithmetic across DST transitions', () => {
    // Madrid spring forward (Sun 2026-03-29) and fall back (Sun 2026-10-25)
    expect(at('2026-03-29T12:00:00Z', 'Europe/Madrid', 'week')).toEqual({ from: '2026-03-23', to: '2026-03-29', tz: 'Europe/Madrid' })
    expect(at('2026-03-29T12:00:00Z', 'Europe/Madrid', '7d').from).toBe('2026-03-23')
    expect(at('2026-10-25T12:00:00Z', 'Europe/Madrid', '7d').from).toBe('2026-10-19')
    // New York fall back (Sun 2026-11-01): 01:30 EST is 06:30Z
    expect(at('2026-11-01T06:30:00Z', 'America/New_York', 'week')).toEqual({ from: '2026-10-26', to: '2026-11-01', tz: 'America/New_York' })
    expect(at('2026-11-01T06:30:00Z', 'America/New_York', '30d').from).toBe('2026-10-03')
    expect(at('2026-11-01T06:30:00Z', 'America/New_York', 'month').from).toBe('2026-11-01')
  })
  it('computes year start in the visitor timezone and clamps to today', () => {
    expect(at('2026-01-01T03:00:00Z', 'America/New_York', 'year')).toEqual({ from: '2025-01-01', to: '2025-12-31', tz: 'America/New_York' })
    expect(at('2026-01-01T03:00:00Z', 'Europe/Madrid', 'year')).toEqual({ from: '2026-01-01', to: '2026-01-01', tz: 'Europe/Madrid' })
    expect(at('2026-11-21T12:00:00Z', 'UTC', 'all')).toEqual({ from: '0001-01-01', to: '2026-11-21', tz: 'UTC' })
  })
  it('builds the rolling 365-day heatmap window ending today in the visitor tz', () => {
    expect(rollingYearRange('UTC', new Date('2026-11-21T12:00:00Z'))).toEqual({ from: '2025-11-22', to: '2026-11-21', tz: 'UTC' })
    expect(rollingYearRange('America/New_York', new Date('2026-01-01T03:00:00Z'))).toEqual({ from: '2025-01-01', to: '2025-12-31', tz: 'America/New_York' })
    expect(rollingYearRange('UTC', new Date('2024-12-31T12:00:00Z')).from).toBe('2024-01-02') // leap year: still 365 days
  })
})

describe('preset URL state', () => {
  const tz = 'UTC'
  const today = new Date('2026-11-21T12:00:00Z') // Saturday
  it('defaults to last 30 days and round-trips every preset through the URL', () => {
    expect(detectPeriod(readFilters('', tz, today), today)).toBe('30d')
    for (const preset of ['today', 'week', '7d', 'month', '30d', 'year', 'all'] as const) {
      const filters = presetRange(preset, tz, today)
      const search = writeFilters(filters, '', preset)
      expect(new URLSearchParams(search).get('preset')).toBe(preset)
      expect(new URLSearchParams(search).get('from')).toBe(filters.from) // absolute dates stay in the link
      expect(new URLSearchParams(search).get('tz')).toBe(tz)
      const back = readFilters(search, 'Asia/Tokyo', today)
      expect(back).toEqual(filters)
      expect(readPeriod(search, back, today)).toBe(preset)
    }
  })
  it('lets the preset param win only while its range still matches, otherwise opens as custom', () => {
    expect(readPeriod('?preset=week&from=2026-11-16&to=2026-11-21&tz=UTC', { from: '2026-11-16', to: '2026-11-21', tz }, today)).toBe('week')
    expect(readPeriod('?preset=30d&from=2026-10-01&to=2026-10-30&tz=UTC', { from: '2026-10-01', to: '2026-10-30', tz }, today)).toBe('custom')
    expect(readPeriod('?from=2026-11-10&to=2026-11-12&tz=UTC', { from: '2026-11-10', to: '2026-11-12', tz }, today)).toBe('custom')
    expect(readPeriod('?preset=custom&from=2026-11-15&to=2026-11-21&tz=UTC', { from: '2026-11-15', to: '2026-11-21', tz }, today)).toBe('custom')
    expect(readPeriod('?from=2026-11-15&to=2026-11-21&tz=UTC', { from: '2026-11-15', to: '2026-11-21', tz }, today)).toBe('7d')
  })
  it('derives the range from a bare preset param', () => {
    expect(readFilters('?preset=week&tz=UTC', 'Asia/Tokyo', today)).toEqual({ from: '2026-11-16', to: '2026-11-21', tz: 'UTC' })
    expect(readFilters('?preset=bogus', 'UTC', today)).toEqual(presetRange('30d', 'UTC', today))
  })
})
