import { type ChartPath } from '../../api/client'
import { EvolutionChart, PieChart } from '../../components/charts'
import { ChartSection, EmptyState } from './ChartSection'
import { HeatmapPanel } from './HeatmapPanel'
import { MapPanel, type MapPanelProps } from './MapPanel'
import { RankingList } from './RankingList'
import type { Filters } from './filters'

export type ChartPanelProps = Pick<MapPanelProps, 'selectedCountry' | 'onCountrySelect'> & { path: ChartPath; title: string; subtitle: string; filters: Filters; onTimezoneRejected: () => void }

// Thin dispatcher: ChartSection frame + one body component per response shape.
export function ChartPanel({ path, title, subtitle, filters, onTimezoneRejected, selectedCountry, onCountrySelect }: ChartPanelProps) {
  return <ChartSection path={path} title={title} subtitle={subtitle} filters={filters} onTimezoneRejected={onTimezoneRejected}>
    {data => {
      if ('items' in data) return <RankingList data={data} />
      if ('cells' in data) return <HeatmapPanel data={data} />
      if ('countries' in data) return <MapPanel data={data} selectedCountry={selectedCountry} onCountrySelect={onCountrySelect} />
      if ('buckets' in data) return data.series.length && data.buckets.length ? <EvolutionChart data={data} /> : <EmptyState />
      return data.slices.length ? <PieChart data={data} /> : <EmptyState />
    }}
  </ChartSection>
}
