// Pure option builders (no React, no echarts instance): unit-testable with a plain ChartTheme.
import type { components } from '../../api/schema'
import worldGeoJson from '../../assets/maps/world-countries.geo.json'
import markerJson from '../../assets/maps/country-markers.json'
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
  itemGap: 12,
  pageIconColor: t.textMuted,
  pageIconInactiveColor: t.border,
  pageTextStyle: { color: t.textMuted },
  textStyle: { color: t.textMuted, fontFamily: t.font, fontSize: 11 },
})

// ---------- Evolution (stacked areas) ----------
export function buildEvolutionOption(data: EvolutionResponse, t: ChartTheme): Opt {
  const { buckets, series, bucket_unit: unit } = data
  const partialIdx = buckets.findIndex((b) => b.partial)
  const label = (i: number) => (unit === 'month' ? buckets[i].start.slice(0, 7) : buckets[i].start)
  const labelOf = new Map(series.map((s) => [seriesId(s), s.label]))
  const colors = series.map((s, i) => seriesColor(t, i, s.is_other))
  return {
    animation: true,
    animationDuration: 500,
    animationEasing: 'cubicOut',
    color: colors,
    textStyle: { fontFamily: t.font },
    grid: { left: 8, right: 16, top: 16, bottom: 40, containLabel: true },
    legend: { ...legendStyle(t), formatter: (n: string) => labelOf.get(n) ?? n },
    tooltip: {
      ...tooltipStyle(t),
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: t.border, width: 1 } },
      formatter: (params: unknown) => {
        const ps = params as { dataIndex: number; color?: unknown; seriesName: string; value: number }[]
        if (!ps.length) return ''
        const b = buckets[ps[0].dataIndex]
        const head = `${b.start} → ${b.end} (excl.)${b.partial ? ' · partial bucket' : ''}`
        const rows = [...ps].sort((a, c) => c.value - a.value)
        const shown = rows.filter((p) => p.value > 0)
        return [
          tipTitle(t, head),
          ...(shown.length ? shown : rows).map((p) =>
            tipRow(t, {
              color: typeof p.color === 'string' ? p.color : undefined,
              label: labelOf.get(p.seriesName) ?? p.seriesName,
              value: nf.format(p.value),
            }),
          ),
        ].join('')
      },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: buckets.map((_, i) => label(i)),
      axisLabel: { color: t.textMuted, hideOverlap: true },
      axisLine: { lineStyle: { color: t.border } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: t.textMuted },
      splitLine: { lineStyle: { color: t.border, opacity: 0.6 } },
    },
    series: series.map((s, si) => ({
      name: seriesId(s),
      type: 'line',
      stack: 'total',
      smooth: true,
      showSymbol: false,
      symbol: 'none',
      lineStyle: { width: 1, color: colors[si], opacity: 0.9 },
      areaStyle: { color: colors[si], opacity: 0.55 },
      emphasis: { focus: 'series', areaStyle: { opacity: 0.8 }, lineStyle: { width: 1.5 } },
      blur: { areaStyle: { opacity: 0.2 }, lineStyle: { opacity: 0.3 } },
      data: s.values,
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
export function buildPieOption(data: PieResponse, t: ChartTheme): Opt {
  const { slices } = data
  const labelOf = new Map(slices.map((s) => [seriesId(s), s.label]))
  return {
    animation: true,
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
        return tipRow(t, {
          color: typeof x.color === 'string' ? x.color : undefined,
          label: labelOf.get(x.name) ?? x.name,
          value: nf.format(x.value),
          sub: pct(x.data.share),
        })
      },
    },
    legend: { ...legendStyle(t), formatter: (n: string) => labelOf.get(n) ?? n },
    series: [
      {
        type: 'pie',
        radius: ['46%', '70%'],
        center: ['50%', '45%'],
        minAngle: 3,
        padAngle: 2,
        avoidLabelOverlap: true,
        label: { show: false },
        labelLine: { show: false },
        itemStyle: { borderRadius: 6, borderWidth: 0 },
        emphasis: { focus: 'self', scale: true, scaleSize: 8, itemStyle: { shadowBlur: 12, shadowColor: 'rgb(0 0 0 / 35%)' } },
        blur: { itemStyle: { opacity: 0.4 } },
        data: slices.map((s, i) => ({
          name: seriesId(s),
          value: s.listens,
          share: s.share,
          itemStyle: { color: seriesColor(t, i, s.is_other) },
        })),
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
  return tipTitle(t, `${wd} ${v[3]}`) + tipRow(t, { label: v[2] === 1 ? 'listen' : 'listens', value: nf.format(v[2]) })
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

export function buildCountryOption(
  data: CountryResponse,
  t: ChartTheme,
  selected: string | null = null,
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
        symbolSize: isSel ? 15 : 11,
        itemStyle: {
          color: mapColor(t, c.listens, max),
          borderColor: isSel ? t.accent : t.text,
          borderWidth: isSel ? 3 : 1.5,
        },
      }
    })
  const hover = mixColors(t.surface2, t.border, 0.6)
  return {
    animation: true,
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
        return tipRow(t, { color: mapColor(t, v, max), label: countryName(code), value: nf.format(v), sub: pct(x.data.share) })
      },
    },
    geo: {
      map: MAP_NAME,
      nameProperty: 'code',
      roam: false,
      layoutCenter: ['50%', '50%'],
      layoutSize: '100%',
      itemStyle: { areaColor: noDataColor(t), borderColor: t.border, borderWidth: 0.5 },
      emphasis: { label: { show: false }, itemStyle: { areaColor: hover } },
      select: { disabled: true },
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
                areaColor: mixColors(mapColor(t, c.listens, max), '#ffffff', 0.18),
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
