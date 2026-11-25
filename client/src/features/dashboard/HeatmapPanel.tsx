import { lazy, Suspense } from 'react'
import type { components } from '../../api/schema'
import { EmptyState } from './ChartSection'
import { HeatmapSkeleton } from './ChartSkeleton'

const HeatmapChart = lazy(() => import('../../components/charts').then(m => ({ default: m.HeatmapChart })))

// Extension point (C1 filters hook / C2 chart): rolling last-365-day heatmap body (no year logic).
// The chart (and echarts) load lazily; the empty-grid skeleton holds its place meanwhile.
export function HeatmapPanel({ data }: { data: components['schemas']['HeatmapResponse'] }) {
  if (!data.cells.some(cell => cell.listens > 0)) return <EmptyState />
  return <Suspense fallback={<HeatmapSkeleton label="Daily listening" from={data.range.from} to={data.range.to} />}><HeatmapChart data={data} /></Suspense>
}
