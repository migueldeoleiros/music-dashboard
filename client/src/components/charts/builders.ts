// Pure option builders (no React, no echarts instance): unit-testable with a plain ChartTheme.
import type { components } from '../../api/schema'
import worldGeoJson from '../../assets/maps/world-countries.geo.json'
import markerJson from '../../assets/maps/country-markers.json'
import { currentMetric, formatMetric } from '../../app/metric'
import type { Opt } from './useECharts'
import { esc, mixColors, seriesColor, tipRow, tipTitle, tooltipStyle, type ChartTheme } from './theme'

type EvolutionResponse = components['schemas']['EvolutionResponse']
type PieResponse = components['schemas']['PieResponse']
type CountryResponse = components['schemas']['CountryResponse']

export const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })
export const pct = (share: number) => `${nf.format(share * 100)}%`

/** Unique, isolating series/slice identity: real keys never collide with the synthetic Other. */
export const seriesId = (x: { key: string; is_other: boolean }) => `${x.is_other ? 'other' : 'key'}:${x.key}`

const legendStyle = (t: ChartTheme) => ({
  type: 'scroll' as const,
  bottom: 0,
  icon: 'circle',
  itemWidth: 8,
  itemHeight: 8,
  itemGap: t.narrow ? 10 : 12,
  pageIconSize: t.narrow ? 16 : 12,
  pageIconColor: t.textMuted,
  pageIconInactiveColor: t.border,
  pageTextStyle: { color: t.textMuted },
  textStyle: { color: t.textMuted, fontFamily: t.font, fontSize: t.narrow ? 12 : 11 },
})

/** Only the largest named pie slices get an outer label + leader line; the rest live in the legend and tooltips. */
export const PIE_LABEL_COUNT = 5
const LEGEND_ROW = 20
const NARROW_LEGEND_ROWS = 6
/** Vertical legend height: one row per entry, capped so longer lists scroll (plus room for the pager). */
export const verticalLegendHeight = (entries: number, maxRows: number) => {
  const n = Math.max(1, entries)
  return Math.min(n, maxRows) * LEGEND_ROW + (n > maxRows ? 28 : 0)
}
const clip = (text: string, max: number) => (text.length > max ? `${text.slice(0, max - 1)}…` : text)

/** Shared vertical, scrollable legend: right of the plot on wide screens, below it on narrow ones. */
function verticalLegend(t: ChartTheme, entries: number, wideRows: number, labelOf: Map<string, string>) {
  const narrow = t.narrow
  return {
    ...legendStyle(t),
    orient: 'vertical' as const,
    ...(narrow
      ? { left: 'center', bottom: 4, height: verticalLegendHeight(entries, NARROW_LEGEND_ROWS) }
      : { right: 8, top: 'middle', height: verticalLegendHeight(entries, wideRows) }),
    itemGap: 6,
    pageIconSize: 12,
    textStyle: {
      color: t.textMuted,
      fontFamily: t.font,
      fontSize: narrow ? 12 : 11,
      width: narrow ? 240 : LEGEND_WIDTH - 24,
      overflow: 'truncate' as const,
    },
    tooltip: { show: false },
    formatter: (n: string) => labelOf.get(n) ?? n,
  }
}
const LEGEND_WIDTH = 150

/** Canvas height of an evolution chart: the plot keeps its size; narrow screens add the legend below. */
export const evolutionNarrowHeight = (series: number) => 250 + verticalLegendHeight(series, NARROW_LEGEND_ROWS)

// ---------- Evolution (stacked areas) ----------
/** `hover` holds the series name under the pointer/finger; the tooltip shows only that series. */
/**
 * The stacked band a point falls in: `x` is the (fractional) bucket index, `y` the value on the axis. Cumulative
 * totals are interpolated between buckets, mirroring the drawn areas, so it never needs polygon hit-testing.
 * Returns the series id, or null above the stack / below zero.
 */
export function seriesAtValue(series: EvolutionResponse['series'], x: number, y: number): string | null {
  const n = series[0]?.values.length ?? 0
  if (n === 0 || y < 0 || !Number.isFinite(x) || !Number.isFinite(y)) return null
  const at = Math.min(Math.max(x, 0), n - 1)
  const lo = Math.floor(at)
  const hi = Math.min(lo + 1, n - 1)
  const f = at - lo
  let floor = 0
  for (const s of series) {
    if (!Number.isFinite(s.values[lo]) || !Number.isFinite(s.values[hi])) return null // gap
    const top = floor + s.values[lo] * (1 - f) + s.values[hi] * f
    if (y >= floor && y < top) return seriesId(s)
    floor = top
  }
  return null
}

/** Weighted listens per bucket. A zero total means a day (or week/month) with no music recorded. */
export const bucketTotals = (series: EvolutionResponse['series'], buckets: number): number[] =>
  Array.from({ length: buckets }, (_, i) => series.reduce((sum, s) => sum + Math.max(0, s.values[i] ?? 0), 0))

/** Each bucket as shares of its own total, 0..100. A bucket without listens stays all zeros (a gap, never a fake 100%). */
export function normalizeShares(series: EvolutionResponse['series']): number[][] {
  const totals = bucketTotals(series, series[0]?.values.length ?? 0)
  return series.map((s) => s.values.map((v, i) => (totals[i] > 0 ? (Math.max(0, v) / totals[i]) * 100 : 0)))
}

/** Sub-steps per bucket gap: enough to look smooth, capped so long ranges stay cheap. */
export const shareSteps = (buckets: number) => (buckets <= 1 ? 1 : Math.max(1, Math.min(8, Math.floor(600 / (buckets - 1)))))

/**
 * Monotone cubic (Fritsch–Carlson) resample at `steps` points per gap. Never overshoots its neighbours,
 * so non-negative input stays non-negative.
 */
export function resampleMonotone(values: number[], steps: number): number[] {
  const n = values.length
  if (n < 2 || steps <= 1) return values.slice()
  const d = Array.from({ length: n - 1 }, (_, i) => values[i + 1] - values[i])
  const m = values.map((_, i) => (i === 0 ? d[0] : i === n - 1 ? d[n - 2] : d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2))
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue }
    const a = m[i] / d[i]
    const b = m[i + 1] / d[i]
    const s = a * a + b * b
    if (s > 9) { const k = 3 / Math.sqrt(s); m[i] = k * a * d[i]; m[i + 1] = k * b * d[i] }
  }
  const out: number[] = []
  for (let i = 0; i < n - 1; i++) {
    for (let j = 0; j < steps; j++) {
      const u = j / steps
      const u2 = u * u
      const u3 = u2 * u
      const v = (2 * u3 - 3 * u2 + 1) * values[i] + (u3 - 2 * u2 + u) * m[i] + (-2 * u3 + 3 * u2) * values[i + 1] + (u3 - u2) * m[i + 1]
      out.push(Math.max(0, v))
    }
  }
  out.push(values[n - 1])
  return out
}

/** Hover sentinel for the pointer sitting in an empty-bucket gap: `gap:<bucket index>`. */
export const GAP_PREFIX = 'gap:'

/** Empty bucket index when `x` (fractional bucket index) lies within half a bucket of it, else null. */
export function gapBucketAt(totals: number[], x: number): number | null {
  if (totals.length === 0 || !Number.isFinite(x)) return null
  const i = Math.min(Math.max(Math.round(x), 0), totals.length - 1)
  return totals[i] <= 0 ? i : null
}

/** [first, last] bucket indexes of each run of empty buckets. */
export function emptyRuns(totals: number[]): [number, number][] {
  const runs: [number, number][] = []
  for (let i = 0; i < totals.length; i++) {
    if (totals[i] > 0) continue
    let j = i
    while (j + 1 < totals.length && totals[j + 1] <= 0) j++
    runs.push([i, j])
    i = j
  }
  return runs
}

/**
 * Smooth 100% stack: shares are resampled per series, then each sub-step is re-normalized to 100.
 * Smoothing the shares, not the stacked boundaries, keeps areas from crossing: boundaries are running sums of non-negatives.
 * Buckets with no listens are real gaps (null): each run of buckets with data is smoothed on its own so nothing ramps
 * to zero across a gap; edges are held flat to the middle of the empty bucket so the gap is one bucket wide.
 */
export function smoothShares(series: EvolutionResponse['series'], buckets: number): (number | null)[][] {
  const steps = shareSteps(buckets)
  const shares = normalizeShares(series)
  const totals = bucketTotals(series, buckets)
  const len = buckets <= 1 ? buckets : (buckets - 1) * steps + 1
  const out = series.map(() => Array<number | null>(len).fill(null))
  for (let i = 0; i < buckets; i++) {
    if (totals[i] <= 0) continue
    let j = i
    while (j + 1 < buckets && totals[j + 1] > 0) j++
    const cols = shares.map((v) => resampleMonotone(v.slice(i, j + 1), steps))
    for (let k = 0; k < cols[0].length; k++) {
      const total = cols.reduce((sum, c) => sum + c[k], 0)
      cols.forEach((c, si) => { out[si][i * steps + k] = total > 0 ? (c[k] / total) * 100 : 0 })
    }
    // Hold the edge shares flat up to the middle of an adjacent empty bucket, so the hatched band is exactly one bucket
    // wide per empty bucket (no blank transition strip) and a lone bucket stays visible as a bar.
    const half = Math.floor(steps / 2)
    const fill = (from: number, to: number, src: number) => {
      for (let k = from; k <= to; k++) if (k >= 0 && k < len) series.forEach((_, si) => { out[si][k] = out[si][src] })
    }
    if (i > 0) fill(i * steps - half, i * steps - 1, i * steps)
    if (j < buckets - 1) fill(j * steps + 1, j * steps + half, j * steps)
    i = j
  }
  return out
}

/** Series with smoothed share values (NaN in gaps), for hit-testing with `seriesAtValue` at x * shareSteps(buckets). */
export const smoothedSeries = (series: EvolutionResponse['series'], buckets: number): EvolutionResponse['series'] => {
  const smooth = smoothShares(series, buckets)
  return series.map((s, i) => ({ ...s, values: smooth[i].map((v) => v ?? Number.NaN) }))
}

/**
 * Diagonal line hatch for buckets with no data: reads as "nothing recorded", distinct from any series.
 * A repeating canvas pattern as the markArea fill (plain tint where canvas is unavailable, e.g. jsdom).
 */
export function gapFill(t: ChartTheme): string | { image: HTMLCanvasElement; repeat: 'repeat'; scaleX: number; scaleY: number } {
  if (typeof document === 'undefined') return t.surface2
  const dpr = Math.max(1, Math.round(globalThis.devicePixelRatio || 1))
  const size = 8 * dpr
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const g = canvas.getContext('2d')
  if (!g) return t.surface2
  g.fillStyle = t.surface2
  g.fillRect(0, 0, size, size)
  g.strokeStyle = mixColors(t.textMuted, t.surface2, 0.45)
  g.lineWidth = dpr
  g.beginPath()
  for (const o of [-size, 0, size]) { g.moveTo(o, size); g.lineTo(o + size, 0) } // seamless 45deg tiles
  g.stroke()
  return { image: canvas, repeat: 'repeat', scaleX: 1 / dpr, scaleY: 1 / dpr }
}

export function buildEvolutionOption(data: EvolutionResponse, t: ChartTheme, hover?: { current: string | null }): Opt {
  const { buckets, series, bucket_unit: unit } = data
  const partialIdx = buckets.findIndex((b) => b.partial)
  const label = (i: number) => (unit === 'month' ? buckets[i].start.slice(0, 7) : buckets[i].start)
  const labelOf = new Map(series.map((s) => [seriesId(s), s.label]))
  const colors = series.map((s, i) => seriesColor(t, i, s.is_other))
  const steps = shareSteps(buckets.length)
  const smooth = smoothShares(series, buckets.length)
  const totals = bucketTotals(series, buckets.length)
  const gaps = emptyRuns(totals).map(([a, b]) => [{ xAxis: Math.max(0, a - 0.5) }, { xAxis: Math.min(buckets.length - 1, b + 0.5) }])
  const seriesByName = new Map(series.map((s) => [seriesId(s), s]))
  return {
    animation: !t.reducedMotion,
    animationDuration: 500,
    animationEasing: 'cubicOut',
    color: colors,
    textStyle: { fontFamily: t.font },
    grid: {
      left: t.narrow ? 4 : 8,
      right: t.narrow ? 10 : LEGEND_WIDTH + 16,
      top: 16,
      bottom: t.narrow ? verticalLegendHeight(series.length, NARROW_LEGEND_ROWS) + 12 : 24,
      containLabel: true,
    },
    legend: verticalLegend(t, series.length, 15, labelOf),
    tooltip: {
      ...tooltipStyle(t),
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: t.border, width: 1 } },
      // Only the highlighted area: its date, label and value. Without a hovered area there is nothing to show.
      formatter: (params: unknown) => {
        const ps = params as { dataIndex: number; color?: unknown; seriesName: string }[]
        if (hover?.current?.startsWith(GAP_PREFIX)) {
          const g = buckets[Number(hover.current.slice(GAP_PREFIX.length))]
          return g ? tipTitle(t, `${g.start} → ${g.end} (excl.)`) + `<div style="color:${esc(t.textMuted)}">No data</div>` : ''
        }
        const p = ps.find((x) => x.seriesName === hover?.current)
        if (!p) return ''
        const i = Math.min(buckets.length - 1, Math.round(p.dataIndex / steps))
        const b = buckets[i]
        const listens = seriesByName.get(p.seriesName)?.values[i] ?? 0
        const head = `${b.start} → ${b.end} (excl.)${b.partial ? ' · partial bucket' : ''}`
        return (
          tipTitle(t, head) +
          tipRow(t, {
            color: typeof p.color === 'string' ? p.color : undefined,
            label: labelOf.get(p.seriesName) ?? p.seriesName,
            value: pct(totals[i] > 0 ? listens / totals[i] : 0),
            sub: `${nf.format(listens)} listens`,
          })
        )
      },
    },
    xAxis: {
      type: 'value',
      min: 0,
      max: Math.max(1, buckets.length - 1),
      minInterval: 1,
      axisLabel: { color: t.textMuted, hideOverlap: true, formatter: (v: number) => (buckets[Math.round(v)] ? label(Math.round(v)) : '') },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: t.border } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      interval: 25,
      axisLabel: { color: t.textMuted, formatter: '{value}%' },
      splitLine: { lineStyle: { color: t.border, opacity: 0.6 } },
    },
    series: series.map((s, si) => ({
      name: seriesId(s),
      type: 'line',
      stack: 'total',
      smooth: false, // already smoothed in share space; ECharts' own bezier could overshoot and cross neighbours
      showSymbol: false,
      symbol: 'none',
      lineStyle: { width: 1, color: colors[si], opacity: 0.9 },
      areaStyle: { color: colors[si], opacity: 0.55 },
      emphasis: { focus: 'series', areaStyle: { opacity: 0.8 }, lineStyle: { width: 1.5 } },
      blur: { areaStyle: { opacity: 0.2 }, lineStyle: { opacity: 0.3 } },
      data: smooth[si].map((v, i) => [i / steps, v]),
      ...(si === 0 && gaps.length
        ? { markArea: { silent: true, itemStyle: { color: gapFill(t), opacity: 1 }, data: gaps } }
        : {}),
      ...(si === 0 && partialIdx >= 0
        ? {
            markLine: {
              silent: true,
              symbol: 'none',
              lineStyle: { type: 'dashed', color: t.textMuted, width: 1 },
              label: { color: t.textMuted, show: true, formatter: 'incomplete', position: 'insideEndTop' },
              data: [{ xAxis: partialIdx }],
            },
          }
        : {}),
    })),
  }
}

// ---------- Pie / donut ----------
/** Canvas height at the mobile breakpoint: the scrollable vertical legend sits under the pie. */
export const pieNarrowHeight = (slices: number) => 232 + verticalLegendHeight(slices, NARROW_LEGEND_ROWS)

/** Hatch for the synthetic "Other": reads as "combined remainder", never as one more real entity. */
const otherDecal = (t: ChartTheme) => ({
  symbol: 'rect',
  rotation: Math.PI / 4,
  dashArrayX: [1, 0],
  dashArrayY: [3, 4],
  symbolSize: 1,
  color: mixColors(t.other, t.surface, 0.55),
})

/**
 * Indexes of the slices that get an external label: the top `PIE_LABEL_COUNT` named (non-Other) slices by the
 * server ranking (listens desc, key asc). Input order is the tie-break of last resort, so the result is stable.
 */
export function labelledPieIndexes(slices: PieResponse['slices']): Set<number> {
  return new Set(
    slices
      .map((s, i) => ({ s, i }))
      .filter(({ s }) => !s.is_other)
      .sort((a, b) => b.s.listens - a.s.listens || (a.s.key < b.s.key ? -1 : a.s.key > b.s.key ? 1 : 0) || a.i - b.i)
      .slice(0, PIE_LABEL_COUNT)
      .map(({ i }) => i),
  )
}

/**
 * Pie geometry in pixels: the largest outer radius that still leaves room for leader-line labels
 * (line + clipped text) beside the pie and a margin above/below it. Falls back to the old fixed size without a measured canvas.
 */
export function pieGeometry(t: ChartTheme, slices: number, size?: { width: number; height: number }) {
  const narrow = t.narrow
  const labelRoom = narrow ? 92 : 130 // leader line (6/10 x2) + up to 12/16 clipped chars + share
  const vMargin = 22
  if (!size || size.width <= 0 || size.height <= 0) {
    return { radius: narrow ? [28, 52] : ['24%', '40%'], center: narrow ? ['50%', 112] : ['32%', '50%'] }
  }
  const plotW = narrow ? size.width : size.width - LEGEND_WIDTH - 24
  const plotH = narrow ? size.height - verticalLegendHeight(slices, NARROW_LEGEND_ROWS) - 12 : size.height
  const outer = Math.round(Math.max(narrow ? 40 : 60, Math.min(plotH / 2 - vMargin, plotW / 2 - labelRoom)))
  return { radius: [Math.round(outer * 0.55), outer], center: [Math.round(plotW / 2), Math.round(plotH / 2)] }
}

export function buildPieOption(data: PieResponse, t: ChartTheme, size?: { width: number; height: number }): Opt {
  const { slices } = data
  const labelOf = new Map(slices.map((s) => [seriesId(s), s.label]))
  const isOther = new Map(slices.map((s) => [seriesId(s), s.is_other]))
  const labelled = labelledPieIndexes(slices)
  const narrow = t.narrow
  const fontSize = narrow ? 12 : 11
  return {
    animation: !t.reducedMotion,
    animationDuration: 500,
    animationEasing: 'cubicOut',
    animationDurationUpdate: 300,
    animationEasingUpdate: 'cubicInOut',
    textStyle: { fontFamily: t.font },
    tooltip: {
      ...tooltipStyle(t),
      trigger: 'item',
      formatter: (p: unknown) => {
        const x = p as { name: string; value: number; color?: unknown; data: { share: number } }
        const row = tipRow(t, {
          color: typeof x.color === 'string' ? x.color : undefined,
          label: labelOf.get(x.name) ?? x.name,
          value: nf.format(x.value),
          sub: pct(x.data.share),
        })
        return isOther.get(x.name) ? row + `<div style="color:${esc(t.textMuted)};margin-top:4px">All remaining entries combined</div>` : row
      },
    },
    // Every slice is in the scrollable vertical legend; only the top five named slices get a leader-line label (title + share).
    legend: verticalLegend(t, slices.length, 14, labelOf),
    series: [
      {
        type: 'pie',
        ...pieGeometry(t, slices.length, size),
        minAngle: 3,
        padAngle: 2,
        avoidLabelOverlap: true,
        label: {
          show: true,
          position: 'outside',
          color: t.textMuted,
          fontFamily: t.font,
          fontSize,
          overflow: 'truncate',
          formatter: (p: unknown) => {
            const x = p as { name: string; data: { share: number } }
            return `${clip(labelOf.get(x.name) ?? x.name, narrow ? 12 : 16)} ${pct(x.data.share)}`
          },
        },
        labelLine: { show: true, length: narrow ? 6 : 10, length2: narrow ? 6 : 10, smooth: 0.2, lineStyle: { color: t.textMuted, opacity: 0.6, width: 1 } },
        labelLayout: { moveOverlap: 'shiftY' },
        itemStyle: { borderRadius: 6, borderWidth: 0 },
        emphasis: { focus: 'self', scale: true, scaleSize: 6, itemStyle: { shadowBlur: 12, shadowColor: t.shadow } },
        blur: { itemStyle: { opacity: 0.4 } },
        data: slices.map((s, i) => {
          const labelShown = labelled.has(i)
          return {
            name: seriesId(s),
            value: s.listens,
            share: s.share,
            label: { show: labelShown },
            labelLine: { show: labelShown },
            itemStyle: { color: seriesColor(t, i, s.is_other), ...(s.is_other ? { decal: otherDecal(t), opacity: 0.85 } : {}) },
          }
        }),
      },
    ],
  }
}

// ---------- Heatmap (rolling window) ----------
export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DAY_MS = 86400000

// Pure UTC civil-date arithmetic: no local-time parsing, safe for any browser TZ.
export function epochDay(iso: string): number {
  return Math.round((Date.parse(`${iso}T12:00:00Z`) - 12 * 3600000) / DAY_MS)
}
export function isoOf(day: number): string {
  return new Date(day * DAY_MS).toISOString().slice(0, 10)
}
/** Monday=0 .. Sunday=6 (1970-01-01 was a Thursday). */
export function weekdayOf(day: number): number {
  return (((day + 3) % 7) + 7) % 7
}

export type HeatPoint = [col: number, row: number, listens: number, iso: string]

/**
 * GitHub-style grid for an arbitrary [from, to] civil-date window (inclusive): weeks are
 * columns, Monday is row 0. Days missing from `cells` count as 0 listens.
 */
export function buildRollingGrid(cells: { day: string; listens: number }[], from: string, to: string) {
  const points: HeatPoint[] = []
  if (from > to) return { points, monthLabels: [] as string[], cols: 0 }
  const values = new Map(cells.map((c) => [c.day, c.listens]))
  const start = epochDay(from)
  const end = epochDay(to)
  const firstWeek = start - weekdayOf(start)
  const cols = (end - weekdayOf(end) - firstWeek) / 7 + 1
  const monthLabels: string[] = Array.from({ length: cols }, () => '')
  for (let d = start; d <= end; d++) {
    const col = (d - weekdayOf(d) - firstWeek) / 7
    const iso = isoOf(d)
    points.push([col, weekdayOf(d), values.get(iso) ?? 0, iso])
    if (iso.endsWith('-01')) monthLabels[col] = MONTHS[Number(iso.slice(5, 7)) - 1]
  }
  // Label the first column with its month only when the next label is not crowding it.
  const next = monthLabels.findIndex((l) => l !== '')
  if (monthLabels[0] === '' && (next === -1 || next >= 3)) monthLabels[0] = MONTHS[Number(from.slice(5, 7)) - 1]
  return { points, monthLabels, cols }
}

/** Share of the accent mixed into the empty color, per intensity level 0..4. */
export const HEAT_LEVELS = [0, 0.3, 0.5, 0.75, 1]

export function heatLevel(listens: number, max: number): number {
  if (listens <= 0 || max <= 0) return 0
  return Math.min(4, Math.max(1, Math.ceil((listens / max) * 4)))
}

export function heatColor(t: ChartTheme, listens: number, max: number): string {
  const level = heatLevel(listens, max)
  return level === 0 ? t.surface2 : mixColors(t.surface2, t.accent, HEAT_LEVELS[level])
}

export function heatTooltip(t: ChartTheme, v: HeatPoint): string {
  const wd = WEEKDAYS[v[1]] ?? ''
  const minutes = currentMetric() === 'minutes'
  return tipTitle(t, `${wd} ${v[3]}`) + tipRow(t, { label: minutes ? 'time' : v[2] === 1 ? 'listen' : 'listens', value: formatMetric(v[2]) })
}

export function buildHeatmapOption(
  data: components['schemas']['HeatmapResponse'],
  t: ChartTheme,
): Opt {
  const grid = buildRollingGrid(data.cells, data.range.from, data.range.to)
  const max = data.cells.reduce((m, c) => Math.max(m, c.listens), 0)
  return {
    animation: false,
    textStyle: { fontFamily: t.font },
    tooltip: {
      ...tooltipStyle(t),
      formatter: (p: unknown) => heatTooltip(t, (p as { value: HeatPoint }).value),
    },
    grid: { top: 22, left: 32, right: 4, bottom: 2 },
    xAxis: {
      type: 'category',
      position: 'top',
      data: grid.monthLabels.map((_, i) => String(i)),
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: {
        color: t.textMuted,
        interval: 0,
        align: 'left',
        formatter: (v: string) => grid.monthLabels[Number(v)] ?? '',
      },
    },
    yAxis: {
      type: 'category',
      inverse: true, // Monday on top
      data: WEEKDAYS,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: t.textMuted, interval: 0, formatter: (v: string) => (['Mon', 'Wed', 'Fri'].includes(v) ? v : '') },
    },
    series: [
      {
        type: 'heatmap',
        data: grid.points.map((p) => ({ value: p, itemStyle: { color: heatColor(t, p[2], max) } })),
        itemStyle: { borderWidth: 2, borderColor: t.surface, borderRadius: 3 },
        emphasis: { itemStyle: { borderColor: t.accent, borderWidth: 1.5 } },
      },
    ],
  }
}

// ---------- Country map ----------
export const MAP_NAME = 'natural-earth-countries'

type Feature = { properties: { code: string; name: string } }
const features = (worldGeoJson as unknown as { features: Feature[] }).features
export const polygonCodes = new Set(features.map((f) => f.properties.code))
export const countryNames = new Map(features.map((f) => [f.properties.code, f.properties.name]))
export const markers = markerJson as unknown as Record<string, [number, number, string]>

function regionName(code: string): string {
  try {
    return new Intl.DisplayNames(['en'], { type: 'region' }).of(code) ?? code
  } catch {
    return code
  }
}
export const countryName = (code: string) => countryNames.get(code.toUpperCase()) ?? regionName(code.toUpperCase())

/** Accent gradient for countries with listens: dimmer for few, full accent for the most. */
export function mapColor(t: ChartTheme, listens: number, max: number): string {
  const ratio = max > 0 ? Math.sqrt(Math.min(1, listens / max)) : 1
  return mixColors(t.surface2, t.accent, 0.4 + 0.6 * ratio)
}

/** Muted fill for countries without listens. */
export const noDataColor = (t: ChartTheme) => t.surface2

/** Natural Earth land (without Antarctica) is ~1.56x wider than tall at the geo's native projection. */
const WORLD_ASPECT = 1.56
/**
 * Default world width in px: as wide as the canvas allows (3% margin) but never taller than the
 * canvas (6px margin), so it neither clips on narrow phones nor floats small in wide, short cards.
 * Falls back to ECharts' own fit before the canvas is measured.
 */
export function worldWidth(size?: { width: number; height: number }): number | string {
  if (!size || size.width <= 0 || size.height <= 0) return '100%'
  return Math.floor(Math.min(size.width * 0.97, (size.height - 12) * WORLD_ASPECT))
}

export function buildCountryOption(
  data: CountryResponse,
  t: ChartTheme,
  selected: string | null = null,
  size?: { width: number; height: number },
): Opt {
  const countries = data.countries
  const sel = selected?.toUpperCase() ?? null
  const max = countries.reduce((m, c) => Math.max(m, c.listens), 0)
  const markerPoints = countries
    .filter((c) => !polygonCodes.has(c.code.toUpperCase()) && markers[c.code.toUpperCase()])
    .map((c) => {
      const code = c.code.toUpperCase()
      const m = markers[code]
      const isSel = code === sel
      return {
        name: countryName(code),
        code,
        value: [m[0], m[1], c.listens],
        share: c.share,
        symbolSize: (isSel ? 15 : 11) + (t.coarse ? 8 : 0),
        itemStyle: {
          color: mapColor(t, c.listens, max),
          borderColor: isSel ? t.accent : t.text,
          borderWidth: isSel ? 3 : 1.5,
        },
      }
    })
  return {
    animation: !t.reducedMotion,
    animationDurationUpdate: 250,
    animationEasingUpdate: 'cubicOut',
    textStyle: { fontFamily: t.font },
    tooltip: {
      ...tooltipStyle(t),
      trigger: 'item',
      formatter: (p: unknown) => {
        const x = p as { name: string; data?: { code?: string; value: number | number[]; share: number } }
        if (!x.data) return tipRow(t, { label: countryName(x.name), value: '0', sub: 'no listens' })
        const code = x.data.code ?? x.name
        const v = Array.isArray(x.data.value) ? x.data.value[2] : x.data.value
        return tipRow(t, { color: mapColor(t, v, max), label: countryName(code), value: formatMetric(v), sub: pct(x.data.share) })
      },
    },
    geo: {
      map: MAP_NAME,
      nameProperty: 'code',
      // Touch: pan + pinch. Mouse: drag-pan only (wheel stays page scroll); zoom buttons cover both.
      roam: t.coarse ? true : 'move',
      scaleLimit: { min: 1, max: 8 },
      layoutCenter: ['50%', '50%'],
      layoutSize: worldWidth(size),
      itemStyle: { areaColor: noDataColor(t), borderColor: t.border, borderWidth: 0.5 },
      // No hover restyle for regions without listens (not clickable); listened regions set their own emphasis below.
      emphasis: { label: { show: false }, itemStyle: { areaColor: noDataColor(t), borderColor: t.border, borderWidth: 0.5 } },
      select: { disabled: true },
      // A map series bound by geoIndex does not paint its own regions: the shared geo does, so the
      // choropleth fills must live here (series itemStyle alone leaves every region at the no-data color).
      regions: countries
        .filter((c) => polygonCodes.has(c.code.toUpperCase()))
        .map((c) => {
          const code = c.code.toUpperCase()
          const isSel = code === sel
          const fill = mapColor(t, c.listens, max)
          return {
            name: code,
            itemStyle: { areaColor: fill, borderColor: isSel ? t.accent : t.border, borderWidth: isSel ? 2 : 0.5 },
            emphasis: { itemStyle: { areaColor: mixColors(fill, t.text, 0.18), borderColor: isSel ? t.accent : t.text, borderWidth: isSel ? 2 : 1 } },
          }
        }),
    },
    series: [
      {
        type: 'map',
        geoIndex: 0,
        data: countries.map((c) => {
          const code = c.code.toUpperCase()
          const isSel = code === sel
          return {
            name: code,
            code,
            value: c.listens,
            share: c.share,
            itemStyle: {
              areaColor: mapColor(t, c.listens, max),
              borderColor: isSel ? t.accent : t.border,
              borderWidth: isSel ? 2 : 0.5,
            },
            emphasis: {
              itemStyle: {
                areaColor: mixColors(mapColor(t, c.listens, max), t.text, 0.18),
                borderColor: isSel ? t.accent : t.text,
                borderWidth: isSel ? 2 : 1,
              },
            },
          }
        }),
      },
      {
        type: 'scatter',
        coordinateSystem: 'geo',
        geoIndex: 0,
        symbol: 'circle',
        emphasis: { scale: 1.4 },
        z: 5,
        data: markerPoints,
      },
    ],
  }
}

export { esc }
