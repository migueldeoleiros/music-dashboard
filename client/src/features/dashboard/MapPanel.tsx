import type { components } from '../../api/schema'
import { CountryChart } from '../../components/charts'
import { EmptyState } from './ChartSection'

export type MapPanelProps = {
  data: components['schemas']['CountryResponse']
  /** ISO alpha-2 code of the selected country, if any (C2 renders it, Phase 2 wires it). */
  selectedCountry?: string | null
  /** Called with an ISO alpha-2 code when the user selects a country (C2 emits it). */
  onCountrySelect?: (code: string) => void
}

// Extension point (C2 map / C3 country panel). selectedCountry/onCountrySelect are accepted but
// not forwarded yet: CountryChart gains matching props in C2 and this component wires them then.
export function MapPanel({ data }: MapPanelProps) {
  return data.countries.length ? <CountryChart data={data} /> : <EmptyState />
}
