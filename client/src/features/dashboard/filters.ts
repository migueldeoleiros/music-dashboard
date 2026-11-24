// `metric` is only present for minutes; it is sent to the views that support it and never read from the URL.
export type Filters = { from: string; to: string; tz: string; metric?: 'minutes' }
export type Preset = 'today' | 'week' | '7d' | 'month' | '30d' | 'year' | 'all'
export type Period = Preset | 'custom'
// Detection order matters when ranges coincide (e.g. Monday: today == week).
export const PRESETS: readonly Preset[] = ['today', '7d', '30d', 'week', 'month', 'year', 'all']
const DEFAULT_PRESET: Preset = '7d'
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

export function utcFallback(filters: Filters): Filters {
  // A rejected timezone changes interpretation, never the explicit calendar dates.
  return { ...filters, tz: 'UTC' }
}

// URL contract: presets are live in the browser timezone. Explicit date pairs are fixed,
// including legacy mixed preset/date URLs. Invalid or incomplete pairs fall back to 7 days.
// Legacy tz is ignored. Never infer preset intent from coincidentally matching dates.
export function readFilters(search: string, browserTz = browserTimezone(), now = new Date()): Filters {
  const params = new URLSearchParams(search)
  const tz = browserTz
  const presetParam = params.get('preset')
  const from = params.get('from')
  const to = params.get('to')
  if (from !== null || to !== null) {
    const explicit = { from: from ?? '', to: to ?? '', tz }
    return validateFilters(explicit) ? presetRange(DEFAULT_PRESET, tz, now) : explicit
  }
  return presetRange(isPreset(presetParam) ? presetParam : DEFAULT_PRESET, tz, now)
}

// Which period button is selected for a URL.
export function readPeriod(search: string, filters: Filters): Period {
  const params = new URLSearchParams(search)
  const preset = params.get('preset')
  if (!params.has('from') && !params.has('to')) return isPreset(preset) ? preset : DEFAULT_PRESET
  const explicit = { ...filters, from: params.get('from') ?? '', to: params.get('to') ?? '' }
  return validateFilters(explicit) ? DEFAULT_PRESET : 'custom'
}

export function validateFilters(filters: Filters): string | null {
  if (!validDate(filters.from) || !validDate(filters.to)) return 'Enter valid local dates in YYYY-MM-DD format.'
  if (filters.from > filters.to) return 'From must be on or before To. One-day ranges are supported.'
  if (!filters.tz.trim()) return 'Enter an IANA timezone, for example Europe/Lisbon.'
  return null
}

// Rewrite only filter params, preserving unrelated params and the current hash.
// The default 7d preset can remain a bare URL; every other preset carries its intent.
export function writeFilters(filters: Filters, search = window.location.search, period: Period = 'custom'): string {
  const params = new URLSearchParams(search)
  for (const key of ['from', 'to', 'tz', 'preset']) params.delete(key)
  if (period === 'custom') { params.set('from', filters.from); params.set('to', filters.to) }
  else if (period !== DEFAULT_PRESET) params.set('preset', period)
  const query = params.toString()
  return `${query ? `?${query}` : window.location.pathname}${window.location.hash}`
}
