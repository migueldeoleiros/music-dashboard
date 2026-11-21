export type Filters = { from: string; to: string; tz: string }
export type Preset = 'today' | '7d' | '30d' | 'year' | 'all'

export function browserTimezone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' } catch { return 'UTC' }
}

export function todayInZone(tz: string, now = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now)
    const get = (type: string) => parts.find(part => part.type === type)?.value ?? ''
    return `${get('year').padStart(4, '0')}-${get('month')}-${get('day')}`
  } catch { return now.toISOString().slice(0, 10) }
}

export function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value < '0001-01-01') return false
  const date = new Date(`${value}T12:00:00Z`)
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value
}

// Calendar-only arithmetic. API/PostgreSQL remains authoritative for timezone boundaries.
export function addDays(day: string, amount: number): string {
  const date = new Date(`${day}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + amount)
  return date.toISOString().slice(0, 10)
}

export function presetRange(preset: Preset, tz: string, now = new Date()): Filters {
  const to = todayInZone(tz, now)
  let from = to
  if (preset === '7d') from = addDays(to, -6)
  if (preset === '30d') from = addDays(to, -29)
  if (preset === 'year') from = `${to.slice(0, 4)}-01-01`
  if (preset === 'all') from = '0001-01-01'
  return { from, to, tz }
}

export function clampToToday(filters: Filters, now = new Date()): Filters {
  const today = todayInZone(filters.tz, now)
  return { ...filters, to: validDate(filters.to) && filters.to > today ? today : filters.to }
}

export function changeTimezone(filters: Filters, tz: string, now = new Date()): Filters {
  const next = clampToToday({ ...filters, tz }, now)
  // The new timezone may still be yesterday. Keep a valid one-day range
  // instead of leaving From after the newly clamped To.
  return { ...next, from: validDate(next.from) && next.from > next.to ? next.to : next.from }
}

export function utcFallback(filters: Filters, now = new Date()): Filters {
  return changeTimezone(filters, 'UTC', now)
}

export function readFilters(search: string, browserTz = browserTimezone(), now = new Date()): Filters {
  const params = new URLSearchParams(search)
  const tz = params.get('tz') ?? browserTz
  const defaults = presetRange('30d', tz, now)
  return clampToToday({ from: params.get('from') ?? defaults.from, to: params.get('to') ?? defaults.to, tz }, now)
}

export function validateFilters(filters: Filters): string | null {
  if (!validDate(filters.from) || !validDate(filters.to)) return 'Enter valid local dates in YYYY-MM-DD format.'
  if (filters.from > filters.to) return 'From must be on or before To. One-day ranges are supported.'
  if (!filters.tz.trim()) return 'Enter an IANA timezone, for example Europe/Lisbon.'
  return null
}

export function writeFilters(filters: Filters, search = window.location.search): string {
  const params = new URLSearchParams(search)
  for (const key of ['from', 'to', 'tz'] as const) params.set(key, filters[key])
  return `?${params.toString()}`
}
