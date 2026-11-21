import type { components } from '../../api/schema'
import { HeatmapChart } from '../../components/charts'
import { EmptyState } from './ChartSection'

// Extension point (C1 filters hook / C2 chart): rolling last-365-day heatmap body (no year logic).
export function HeatmapPanel({ data }: { data: components['schemas']['HeatmapResponse'] }) {
  return data.cells.some(cell => cell.listens > 0) ? <HeatmapChart data={data} /> : <EmptyState />
}
