import { lazy, Suspense } from 'react'
import type { components } from '../../api/schema'
import { EmptyState } from './ChartSection'
import { ChartBlockSkeleton } from './ChartSkeleton'

const CountryChart = lazy(() => import('../../components/charts').then(m => ({ default: m.CountryChart })))

export type MapPanelProps = {
  data: components['schemas']['CountryResponse']
  /** ISO alpha-2 code of the selected country, if any; null clears the highlight. */
  selectedCountry?: string | null
  /**
   * Called with an ISO alpha-2 code when the user selects a country that has listens, and with
   * null when the selected country is clicked again (toggle off).
   */
  onCountrySelect?: (code: string | null) => void
}

// Extension point (C2 map / C3 country panel): forwards selection props to CountryChart.
export function MapPanel({ data, selectedCountry, onCountrySelect }: MapPanelProps) {
  if (!data.countries.length) return <EmptyState />
  return <Suspense fallback={<ChartBlockSkeleton label="Artist origins" kind="map" />}><CountryChart data={data} selectedCountry={selectedCountry} onCountrySelect={onCountrySelect} /></Suspense>
}
