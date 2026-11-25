import type { CSSProperties } from 'react'
import { buildRollingGrid, HEAT_LEVELS } from '../../components/charts/builders'
import { EVOLUTION_HEIGHT, HEATMAP_COLUMN, HEATMAP_GUTTER, HEATMAP_HEIGHT, MAP_HEIGHT, MAP_NARROW_HEIGHT, PIE_HEIGHT } from '../../components/charts/sizes'
import { HeroSkeleton, Skeleton } from '../../components/ui/Skeleton'
import type { ChartPath } from '../../api/client'

export type SkeletonKind = 'heatmap' | 'ranking' | 'map' | 'evolution' | 'pie'

export const skeletonKind = (path: ChartPath): SkeletonKind =>
  path.includes('/heatmap') ? 'heatmap' : path.includes('/rankings/') ? 'ranking' : path.includes('/countries') ? 'map' : path.includes('/evolution/') ? 'evolution' : 'pie'

function Status({ label }: { label: string }) {
  return <p role="status" className="sr-only">Loading {label.toLowerCase()}…</p>
}

export function HeatmapSkeleton({ label, from, to }: { label: string; from: string; to: string }) {
  const { cols } = buildRollingGrid([], from, to)
  const style = { '--chart-h': `${HEATMAP_HEIGHT}px`, '--cols': cols, minWidth: cols * HEATMAP_COLUMN + HEATMAP_GUTTER } as CSSProperties
  return <div>
    <Status label={label} />
    <div className="skeleton-heat" aria-hidden="true"><div className="skeleton-heat-grid" style={style}>{Array.from({ length: cols * 7 }, (_, i) => <i key={i} />)}</div></div>
    <div className="heat-legend heat-legend--skeleton" aria-hidden="true"><span>Less</span>{HEAT_LEVELS.map((_, i) => <i key={i} />)}<span>More</span></div>
  </div>
}

export function ChartBlockSkeleton({ label, kind }: { label: string; kind: 'map' | 'evolution' | 'pie' }) {
  const height = { map: MAP_HEIGHT, evolution: EVOLUTION_HEIGHT, pie: PIE_HEIGHT }[kind]
  const style = { '--chart-h': `${height}px`, '--chart-h-narrow': `${kind === 'map' ? MAP_NARROW_HEIGHT : height}px` } as CSSProperties
  return <div><Status label={label} /><Skeleton className="skeleton-chart" style={style} /></div>
}

// Lists have an unknown length: one hero placeholder (always present), the real rows expand in afterwards.
export function RankingSkeleton({ label }: { label: string }) {
  return <><Status label={label} /><ol className="entity-list ranking-entities" aria-hidden="true"><HeroSkeleton /></ol></>
}

/** Pending placeholder for a dashboard section, chosen by the endpoint. */
export function ChartSkeleton({ path, label, from, to }: { path: ChartPath; label: string; from: string; to: string }) {
  const kind = skeletonKind(path)
  if (kind === 'heatmap') return <HeatmapSkeleton label={label} from={from} to={to} />
  if (kind === 'ranking') return <RankingSkeleton label={label} />
  return <ChartBlockSkeleton label={label} kind={kind} />
}
