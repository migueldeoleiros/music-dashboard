import { lazy, Suspense } from 'react'
import { type ChartPath } from '../../api/client'
import { ChartSection, EmptyState, type ChartSectionProps } from './ChartSection'
import { ChartBlockSkeleton } from './ChartSkeleton'
import { HeatmapPanel } from './HeatmapPanel'
import { MapPanel, type MapPanelProps } from './MapPanel'
import { RankingList } from './RankingList'
import type { Filters } from './filters'

const EvolutionChart = lazy(() => import('../../components/charts').then(m => ({ default: m.EvolutionChart })))
const PieChart = lazy(() => import('../../components/charts').then(m => ({ default: m.PieChart })))

export type ChartPanelProps = Pick<MapPanelProps, 'selectedCountry' | 'onCountrySelect'> & { path: ChartPath; label: string; filters: Filters; variant?: ChartSectionProps['variant']; onTimezoneRejected: () => void }

// Thin dispatcher: ChartSection frame + one body component per response shape.
export function ChartPanel({ path, label, filters, variant, onTimezoneRejected, selectedCountry, onCountrySelect }: ChartPanelProps) {
  return <ChartSection path={path} label={label} filters={filters} variant={variant} onTimezoneRejected={onTimezoneRejected}>
    {data => {
      if ('items' in data) return <RankingList data={data} />
      if ('cells' in data) return <HeatmapPanel data={data} />
      if ('countries' in data) return <MapPanel data={data} selectedCountry={selectedCountry} onCountrySelect={onCountrySelect} />
      if ('buckets' in data) return data.series.length && data.buckets.length ? <Suspense fallback={<ChartBlockSkeleton label={label} kind="evolution" />}><EvolutionChart data={data} /></Suspense> : <EmptyState />
      return data.slices.length ? <Suspense fallback={<ChartBlockSkeleton label={label} kind="pie" />}><PieChart data={data} /></Suspense> : <EmptyState />
    }}
  </ChartSection>
}
