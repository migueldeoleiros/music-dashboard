export type Filters = { from: string; to: string; tz: string }
export type Preset = 'today' | 'week' | '7d' | 'month' | '30d' | 'year' | 'all'
export type Period = Preset | 'custom'
// Detection order matters when ranges coincide (e.g. Monday: today == week).
export const PRESETS: readonly Preset[] = ['today', 'week', '7d', 'month', '30d', 'year', 'all']
// Lower bound the API accepts for "all time". Never displayed.
export const ALL_TIME_FROM = '0001-01-01'
export const isPreset = (value: string | null): value is Preset => !!value && (PRESETS as readonly string[]).includes(value)

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

// Monday of the week containing a local calendar day (calendar arithmetic only, so DST never matters).
export function weekStart(day: string): string {
  const weekday = new Date(`${day}T12:00:00Z`).getUTCDay()
  return addDays(day, -((weekday + 6) % 7))
}

export function presetRange(preset: Preset, tz: string, now = new Date()): Filters {
  const to = todayInZone(tz, now)
  let from = to
  if (preset === 'week') from = weekStart(to)
  if (preset === '7d') from = addDays(to, -6)
  if (preset === 'month') from = `${to.slice(0, 8)}01`
  if (preset === '30d') from = addDays(to, -29)
  if (preset === 'year') from = `${to.slice(0, 4)}-01-01`
  if (preset === 'all') from = ALL_TIME_FROM
  return { from, to, tz }
}

// Heatmap window: independent of the period selector. Rolling 365 days ending today in tz.
export function rollingYearRange(tz: string, now = new Date()): Filters {
  const to = todayInZone(tz, now)
  return { from: addDays(to, -364), to, tz }
}

export function detectPeriod(filters: Filters, now = new Date()): Period {
  return PRESETS.find(preset => {
    const range = presetRange(preset, filters.tz, now)
    return range.from === filters.from && range.to === filters.to
  }) ?? 'custom'
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
  const tz = params.get('tz')?.trim() || browserTz
  const presetParam = params.get('preset')
  const defaults = presetRange(isPreset(presetParam) ? presetParam : '30d', tz, now)
  return clampToToday({ from: params.get('from') ?? defaults.from, to: params.get('to') ?? defaults.to, tz }, now)
}

// Which period button is selected for a URL. A preset param only counts while its range still matches;
// anything else (stale shared link, hand-edited dates) opens as Custom.
export function readPeriod(search: string, filters: Filters, now = new Date()): Period {
  const param = new URLSearchParams(search).get('preset')
  if (param === 'custom') return 'custom'
  if (isPreset(param)) {
    const range = presetRange(param, filters.tz, now)
    if (range.from === filters.from && range.to === filters.to) return param
  }
  return detectPeriod(filters, now)
}

export function validateFilters(filters: Filters): string | null {
  if (!validDate(filters.from) || !validDate(filters.to)) return 'Enter valid local dates in YYYY-MM-DD format.'
  if (filters.from > filters.to) return 'From must be on or before To. One-day ranges are supported.'
  if (!filters.tz.trim()) return 'Enter an IANA timezone, for example Europe/Lisbon.'
  return null
}

export function writeFilters(filters: Filters, search = window.location.search, period?: Period): string {
  const params = new URLSearchParams(search)
  for (const key of ['from', 'to', 'tz'] as const) params.set(key, filters[key])
  if (period) params.set('preset', period)
  return `?${params.toString()}`
}
