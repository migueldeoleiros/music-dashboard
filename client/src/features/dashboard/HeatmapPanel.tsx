import type { components } from '../../api/schema'
import { HeatmapChart } from '../../components/charts'
import { EmptyState } from './ChartSection'

// Extension point (C1 filters hook / C2 chart): daily heatmap body.
export function HeatmapPanel({ data }: { data: components['schemas']['HeatmapResponse'] }) {
  return data.cells.some(cell => cell.listens > 0) ? <HeatmapChart data={data} /> : <EmptyState />
}
