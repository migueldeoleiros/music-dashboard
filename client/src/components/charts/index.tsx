import { useId, useMemo, useState, type ReactNode } from 'react'
import type { components } from '../../api/schema'
import worldGeoJson from '../../assets/maps/world-countries.geo.json'
import markerJson from '../../assets/maps/country-markers.json'
import { echarts } from './echarts'
import { stableColor } from './stableColor'
import { useECharts } from './useECharts'

export { stableColor } from './stableColor'

type EvolutionResponse = components['schemas']['EvolutionResponse']
type PieResponse = components['schemas']['PieResponse']
type HeatmapResponse = components['schemas']['HeatmapResponse']
type CountryResponse = components['schemas']['CountryResponse']

const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })
const yy = (y: number) => String(y).padStart(4, '0')
const pct = (share: number) => `${nf.format(share * 100)}%`
const TEXT = '#9aa0aa'
const LINE = '#2c3038'

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

/** Unique, isolating series/slice identity: real keys never collide with the synthetic Other. */
const seriesId = (x: { key: string; is_other: boolean }) => `${x.is_other ? 'other' : 'key'}:${x.key}`

type Opt = Parameters<ReturnType<typeof echarts.init>['setOption']>[0]

function Frame(props: {
  label: string
  summary: string
  height: number
  option: Opt
  note?: ReactNode
  table: ReactNode
  register?: () => void
}) {
  const ref = useECharts(props.option, { register: props.register })
  return (
    <figure className="chart" style={{ margin: 0, minWidth: 0 }}>
      <div
        ref={ref}
        role="img"
        aria-label={`${props.label}. ${props.summary}`}
        style={{ width: '100%', height: props.height, maxWidth: '100%' }}
      />
      {props.note ? <p className="chart-note">{props.note}</p> : null}
      <figcaption>
        <p className="chart-summary">{props.summary}</p>
        <details className="chart-table">
          <summary>Show data table</summary>
          <div style={{ overflowX: 'auto', maxHeight: 320 }}>{props.table}</div>
        </details>
      </figcaption>
    </figure>
  )
}

// ---------- Evolution ----------
export function EvolutionChart({ data }: { data: EvolutionResponse }) {
  const { buckets, series, bucket_unit: unit, dimension } = data
  const partialIdx = buckets.findIndex((b) => b.partial)
  const label = (i: number) => {
    const b = buckets[i]
    return unit === 'month' ? b.start.slice(0, 7) : b.start
  }
  const labelOf = new Map(series.map((s) => [seriesId(s), s.label]))
  const option = useMemo<Opt>(
    () => ({
      animation: false,
      color: series.map((s) => stableColor(s.key, s.is_other)),
      grid: { left: 8, right: 16, top: 16, bottom: 56, containLabel: true },
      legend: { type: 'scroll', bottom: 0, textStyle: { color: TEXT }, formatter: (n: string) => labelOf.get(n) ?? n },
      tooltip: {
        trigger: 'axis',
        confine: true,
        formatter: (params: unknown) => {
          const ps = params as { dataIndex: number; marker: string; seriesName: string; value: number }[]
          if (!ps.length) return ''
          const i = ps[0].dataIndex
          const b = buckets[i]
          const head = `${esc(b.start)} → ${esc(b.end)} (excl.)${b.partial ? ' · partial bucket' : ''}`
          const rows = [...ps]
            .sort((a, c) => c.value - a.value)
            .map((p) => `${p.marker}${esc(labelOf.get(p.seriesName) ?? p.seriesName)}: <b>${nf.format(p.value)}</b>`)
          return [head, ...rows].join('<br/>')
        },
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: buckets.map((_, i) => label(i)),
        axisLabel: { color: TEXT, hideOverlap: true },
        axisLine: { lineStyle: { color: LINE } },
      },
      yAxis: {
        type: 'value',
        name: 'Weighted listens',
        nameTextStyle: { color: TEXT, align: 'left' },
        axisLabel: { color: TEXT },
        splitLine: { lineStyle: { color: LINE } },
      },
      series: series.map((s, si) => ({
        name: seriesId(s),
        type: 'line',
        showSymbol: buckets.length <= 40,
        symbolSize: 4,
        lineStyle: { width: 1.25 },
        emphasis: { focus: 'series', lineStyle: { width: 2.5 } },
        data: s.values,
        ...(si === 0 && partialIdx >= 0
          ? {
              markLine: {
                silent: true,
                symbol: 'none',
                lineStyle: { type: 'dashed', color: TEXT, width: 1 },
                label: { color: TEXT, show: true, formatter: 'incomplete', position: 'insideEndTop' },
                data: [{ xAxis: partialIdx }],
              },
            }
          : {}),
      })),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data],
  )
  const partial = partialIdx >= 0 ? buckets[partialIdx] : null
  return (
    <Frame
      label={`Evolution of ${dimension}, ${series.length} series per ${unit}`}
      summary={`${series.length} series over ${buckets.length} ${unit} buckets from ${data.range.from} to ${data.range.to} (${data.range.tz}). Not cumulative.`}
      height={360}
      option={option}
      note={
        partial
          ? `The final ${unit} bucket (${partial.start} → ${partial.end}) is incomplete, so its values may be lower than complete buckets; compare it with them cautiously.`
          : undefined
      }
      table={
        <table>
          <caption className="sr-only">Weighted listens per {unit}</caption>
          <thead>
            <tr>
              <th scope="col">Bucket start</th>
              {series.map((s) => (
                <th scope="col" key={s.key}>
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {buckets.map((b, i) => (
              <tr key={b.start}>
                <th scope="row">
                  {label(i)}
                  {b.partial ? ' (partial)' : ''}
                </th>
                {series.map((s) => (
                  <td key={s.key}>{nf.format(s.values[i] ?? 0)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      }
    />
  )
}

// ---------- Pie ----------
export function PieChart({ data }: { data: PieResponse }) {
  const { slices, dimension } = data
  const labelOf = new Map(slices.map((s) => [seriesId(s), s.label]))
  const option = useMemo<Opt>(
    () => ({
      animation: false,
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: (p: unknown) => {
          const x = p as { name: string; value: number; data: { share: number }; marker: string }
          return `${x.marker}${esc(labelOf.get(x.name) ?? x.name)}<br/><b>${nf.format(x.value)}</b> listens · ${pct(x.data.share)}`
        },
      },
      legend: { type: 'scroll', bottom: 0, textStyle: { color: TEXT }, formatter: (n: string) => labelOf.get(n) ?? n },
      series: [
        {
          type: 'pie',
          radius: ['35%', '68%'],
          center: ['50%', '45%'],
          minAngle: 2,
          label: { show: false },
          itemStyle: { borderColor: '#14161a', borderWidth: 1 },
          emphasis: { scale: true, scaleSize: 6 },
          data: slices.map((s) => ({
            name: seriesId(s),
            value: s.listens,
            share: s.share,
            itemStyle: { color: stableColor(s.key, s.is_other) },
          })),
        },
      ],
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data],
  )
  return (
    <Frame
      label={`Share of ${dimension}`}
      summary={`${slices.length} slices${slices[0] ? `; largest: ${slices[0].label} (${pct(slices[0].share)})` : ''}.`}
      height={340}
      option={option}
      table={
        <table>
          <caption className="sr-only">Share of {dimension}</caption>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Weighted listens</th>
              <th scope="col">Share</th>
            </tr>
          </thead>
          <tbody>
            {slices.map((s) => (
              <tr key={s.key}>
                <th scope="row">{s.label}</th>
                <td>{nf.format(s.listens)}</td>
                <td>{pct(s.share)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    />
  )
}

// ---------- Heatmap ----------
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DAY_MS = 86400000

// Pure UTC civil-date arithmetic: no local-time parsing, safe for any browser TZ and years 0001..0099.
function epochDay(iso: string): number {
  return Math.round((Date.parse(`${iso}T12:00:00Z`) - 12 * 3600000) / DAY_MS)
}
function isoOf(day: number): string {
  return new Date(day * DAY_MS).toISOString().slice(0, 10)
}
/** Monday=0 .. Sunday=6 (1970-01-01 was a Thursday). */
function weekdayOf(day: number): number {
  return (((day + 3) % 7) + 7) % 7
}

export function buildYearGrid(
  cells: { day: string; listens: number }[],
  year: number,
  rangeFrom: string,
  rangeTo: string,
) {
  const y = yy(year)
  const startIso = rangeFrom > `${y}-01-01` ? rangeFrom : `${y}-01-01`
  const endIso = rangeTo < `${y}-12-31` ? rangeTo : `${y}-12-31`
  const points: [number, number, number, string][] = []
  const monthLabels: string[] = []
  if (startIso > endIso) return { points, monthLabels }
  const values = new Map(cells.map((c) => [c.day, c.listens]))
  const start = epochDay(startIso)
  const end = epochDay(endIso)
  const firstWeek = start - weekdayOf(start)
  let lastMonth = -1
  for (let d = start; d <= end; d++) {
    const col = (d - weekdayOf(d) - firstWeek) / 7
    const row = weekdayOf(d)
    const iso = isoOf(d)
    points.push([col, row, values.get(iso) ?? 0, iso])
    if (monthLabels[col] === undefined) {
      const m = Number(iso.slice(5, 7)) - 1
      monthLabels[col] = m !== lastMonth ? MONTHS[m] : ''
      lastMonth = m
    }
  }
  return { points, monthLabels }
}

export function HeatmapChart({ data }: { data: HeatmapResponse }) {
  const years = useMemo(() => {
    const set = new Set<number>([Number(data.range.from.slice(0, 4)), Number(data.range.to.slice(0, 4))])
    for (const c of data.cells) set.add(Number(c.day.slice(0, 4)))
    return [...set].sort((a, b) => a - b)
  }, [data.cells, data.range.from, data.range.to])
  const latestWithCells = useMemo(
    () => data.cells.reduce((m, c) => Math.max(m, Number(c.day.slice(0, 4))), 0) || years[years.length - 1],
    [data.cells, years],
  )
  const [picked, setPicked] = useState<number | null>(null)
  const year = picked !== null && years.includes(picked) ? picked : latestWithCells
  const max = useMemo(() => data.cells.reduce((m, c) => Math.max(m, c.listens), 0), [data.cells])
  const total = useMemo(() => data.cells.reduce((s, c) => s + c.listens, 0), [data.cells])
  const yearTotal = useMemo(
    () => data.cells.filter((c) => c.day.startsWith(`${yy(year)}-`)).reduce((s, c) => s + c.listens, 0),
    [data.cells, year],
  )
  const yearCells = useMemo(
    () => data.cells.filter((c) => c.day.startsWith(`${yy(year)}-`)),
    [data.cells, year],
  )
  const selectId = useId()

  const grid = useMemo(
    () => buildYearGrid(data.cells, year, data.range.from, data.range.to),
    [data.cells, year, data.range.from, data.range.to],
  )

  const option = useMemo<Opt>(() => {
    return {
      animation: false,
      tooltip: {
        confine: true,
        formatter: (p: unknown) => {
          const v = (p as { value: [number, number, number, string] }).value
          return `${esc(v[3])}<br/><b>${nf.format(v[2])}</b> listens`
        },
      },
      visualMap: {
        min: 0,
        max: Math.max(max, 1),
        dimension: 2,
        type: 'piecewise',
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        itemWidth: 12,
        itemHeight: 12,
        splitNumber: 4,
        textStyle: { color: TEXT },
        inRange: { color: ['#1d2a22', '#0e4429', '#006d32', '#26a641', '#39d353'] },
      },
      grid: { top: 24, left: 36, right: 8, height: 7 * 16 },
      xAxis: {
        type: 'category',
        position: 'top',
        data: grid.monthLabels.map((_, i) => String(i)),
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          color: TEXT,
          interval: 0,
          formatter: (v: string) => grid.monthLabels[Number(v)] ?? '',
        },
      },
      yAxis: {
        type: 'category',
        inverse: true, // Monday on top
        data: WEEKDAYS,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: TEXT, interval: 0 },
      },
      series: [
        {
          type: 'heatmap',
          data: grid.points,
          itemStyle: { borderWidth: 2, borderColor: '#14161a' },
          emphasis: { itemStyle: { borderColor: '#f0b429' } },
        },
      ],
    }
  }, [grid, max])

  return (
    <div style={{ minWidth: 0 }}>
      <div className="chart-controls">
        <label htmlFor={selectId}>Year </label>
        <select id={selectId} value={year} onChange={(e) => setPicked(Number(e.target.value))}>
          {[...years].reverse().map((y) => (
            <option key={y} value={y}>
              {yy(y)}
            </option>
          ))}
        </select>
        <p className="chart-note">
          Selected range {data.range.from} to {data.range.to} ({nf.format(total)} listens in total). The calendar
          and data table show one year at a time: {nf.format(yearTotal)} listens in {yy(year)}.
        </p>
      </div>
      <Frame
        label={`Daily listening heatmap for ${year}, weeks start Monday`}
        summary={`${nf.format(total)} listens across ${data.cells.filter((c) => c.listens > 0).length} active days from ${data.range.from} to ${data.range.to} (${data.range.tz}); busiest day ${nf.format(max)} listens. Showing ${year}.`}
        height={7 * 16 + 24 + 48}
        option={option}
        table={
          <table>
            <caption className="sr-only">Listens per day in {yy(year)}</caption>
            <thead>
              <tr>
                <th scope="col">Day</th>
                <th scope="col">Listens</th>
              </tr>
            </thead>
            <tbody>
              {yearCells.map((c) => (
                <tr key={c.day}>
                  <th scope="row">{c.day}</th>
                  <td>{nf.format(c.listens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        }
      />
    </div>
  )
}

// ---------- Country ----------
const MAP_NAME = 'natural-earth-countries'
let mapRegistered = false
function registerWorldMap() {
  if (mapRegistered) return
  echarts.registerMap(MAP_NAME, worldGeoJson as unknown as Parameters<typeof echarts.registerMap>[1])
  mapRegistered = true
}

const polygonCodes = new Set(
  (worldGeoJson as unknown as { features: { properties: { code: string } }[] }).features.map(
    (f) => f.properties.code,
  ),
)
const markers = markerJson as unknown as Record<string, [number, number, string]>

function regionName(code: string): string {
  try {
    return new Intl.DisplayNames(['en'], { type: 'region' }).of(code) ?? code
  } catch {
    return code
  }
}

const countryNames = new Map(
  (worldGeoJson as unknown as { features: { properties: { code: string; name: string } }[] }).features.map(
    (f) => [f.properties.code, f.properties.name],
  ),
)

export function CountryChart({ data }: { data: CountryResponse }) {
  const countries = data.countries
  const max = countries.reduce((m, c) => Math.max(m, c.listens), 0)
  const markerPoints = useMemo(
    () =>
      countries
        .filter((c) => !polygonCodes.has(c.code.toUpperCase()) && markers[c.code.toUpperCase()])
        .map((c) => {
          const m = markers[c.code.toUpperCase()]
          return {
            name: countryNames.get(c.code.toUpperCase()) ?? regionName(c.code.toUpperCase()),
            value: [m[0], m[1], c.listens],
            share: c.share,
          }
        }),
    [countries],
  )
  const markerCodes = useMemo(() => new Set(markerPoints.map((m) => m.name)), [markerPoints])
  const option = useMemo<Opt>(() => {
    registerWorldMap()
    return {
      animation: false,
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: (p: unknown) => {
          const x = p as { name: string; value?: number | number[]; data?: { value: number | number[]; share: number } }
          if (!x.data) return `${esc(x.name)}<br/>No listens`
          const v = Array.isArray(x.data.value) ? x.data.value[2] : x.data.value
          return `${esc(x.name)}<br/><b>${nf.format(v)}</b> listens · ${pct(x.data.share)}`
        },
      },
      visualMap: {
        min: 0,
        max: Math.max(max, 1),
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        seriesIndex: [0, 1],
        textStyle: { color: TEXT },
        inRange: { color: ['#1f3a4d', '#2f7ea8', '#7fd0f0'] },
      },
      geo: {
        map: MAP_NAME,
        nameProperty: 'code',
        roam: false,
        layoutCenter: ['50%', '46%'],
        layoutSize: '100%',
        itemStyle: { areaColor: '#1b1e24', borderColor: '#3a3f49', borderWidth: 0.5 },
        emphasis: { label: { show: false }, itemStyle: { areaColor: '#f0b429' } },
        select: { disabled: true },
      },
      series: [
        {
          type: 'map',
          geoIndex: 0,
          data: countries.map((c) => ({
            name: c.code.toUpperCase(),
            value: c.listens,
            share: c.share,
          })),
        },
        {
          type: 'scatter',
          coordinateSystem: 'geo',
          geoIndex: 0,
          symbol: 'circle',
          symbolSize: 11,
          itemStyle: { borderColor: '#ffffff', borderWidth: 1.5 },
          emphasis: { scale: 1.4 },
          z: 5,
          data: markerPoints,
        },
      ],
    }
  }, [countries, max, markerPoints])
  const nameOf = (code: string) => countryNames.get(code.toUpperCase()) ?? regionName(code.toUpperCase())
  // Tooltip shows ISO code as name when nameProperty is code: map to readable name.
  const readable = useMemo<Opt>(() => {
    const o = option as { tooltip: { formatter: (p: unknown) => string } }
    return {
      ...option,
      tooltip: {
        ...o.tooltip,
        formatter: (p: unknown) => {
          const x = p as { name: string }
          return o.tooltip.formatter({ ...x, name: markerCodes.has(x.name) ? x.name : nameOf(x.name) })
        },
      },
    } as Opt
  }, [option, markerCodes])
  return (
    <Frame
      label="Listens by artist country"
      summary={`${countries.length} countries${markerPoints.length ? ` (${markerPoints.length} shown as markers because they have no polygon)` : ''}${countries[0] ? `; top: ${nameOf(countries[0].code)} (${pct(countries[0].share)})` : ''}. ${data.partial_credit_note}`}
      height={380}
      option={readable}
      register={registerWorldMap}
      table={
        <table>
          <caption className="sr-only">Weighted listens by country</caption>
          <thead>
            <tr>
              <th scope="col">Country</th>
              <th scope="col">Weighted listens</th>
              <th scope="col">Share</th>
            </tr>
          </thead>
          <tbody>
            {countries.map((c) => (
              <tr key={c.code}>
                <th scope="row">
                  {nameOf(c.code)} ({c.code.toUpperCase()})
                  {!polygonCodes.has(c.code.toUpperCase()) && markers[c.code.toUpperCase()]
                    ? ' — shown as marker on map'
                    : ''}
                  {!polygonCodes.has(c.code.toUpperCase()) && !markers[c.code.toUpperCase()]
                    ? ' — not drawn on map'
                    : ''}
                </th>
                <td>{nf.format(c.listens)}</td>
                <td>{pct(c.share)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    />
  )
}
