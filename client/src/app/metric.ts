import { useSyncExternalStore } from 'react'

// What the dashboard counts: listens (default) or listening time. Shared by every view and
// remembered across visits, so the artist pages follow the dashboard toggle.
export type Metric = 'listens' | 'minutes'
const storageKey = 'music-metric'
const changed = 'music-metric-change'

export function currentMetric(): Metric {
  try { return localStorage.getItem(storageKey) === 'minutes' ? 'minutes' : 'listens' } catch { return 'listens' }
}

export function setMetric(metric: Metric) {
  try { localStorage.setItem(storageKey, metric) } catch { /* Storage is optional. */ }
  window.dispatchEvent(new Event(changed))
}

const subscribe = (notify: () => void) => {
  window.addEventListener(changed, notify)
  window.addEventListener('storage', notify)
  return () => { window.removeEventListener(changed, notify); window.removeEventListener('storage', notify) }
}

export const useMetric = (): Metric => useSyncExternalStore(subscribe, currentMetric, () => 'listens')

const count = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })

/** "45m", "3h 05m", "120h": whole minutes, hours once they pass 60. */
export function formatMinutes(value: number): string {
  const total = Math.round(value)
  if (total < 60) return `${total}m`
  const hours = Math.floor(total / 60)
  return hours >= 100 ? `${hours}h` : `${hours}h ${String(total % 60).padStart(2, '0')}m`
}

export const formatMetric = (value: number, metric: Metric = currentMetric()): string => metric === 'minutes' ? formatMinutes(value) : count.format(value)

/** Query-string addition for endpoints that support minutes. Listens stays the bare default. */
export const metricParams = (metric: Metric): Record<string, string> => metric === 'minutes' ? { metric } : {}
