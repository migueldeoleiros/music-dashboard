import { describe, expect, it } from 'vitest'
import { addDays, clampToToday, presetRange, readFilters, todayInZone, utcFallback, validDate, validateFilters, writeFilters } from '../src/features/dashboard/filters'

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
