import { useCallback, useEffect, useMemo, useRef, type CSSProperties, type ReactNode } from 'react'
import type { components } from '../../api/schema'
import worldGeoJson from '../../assets/maps/world-countries.geo.json'
import {
  buildCountryOption,
  buildEvolutionOption,
  buildHeatmapOption,
  buildPieOption,
  buildRollingGrid,
  countryName,
  HEAT_LEVELS,
  MAP_NAME,
  markers,
  nf,
  pct,
  polygonCodes,
} from './builders'
import { echarts } from './echarts'
import { useECharts, type ChartHandle, type Opt } from './useECharts'
import type { ChartTheme } from './theme'

export { stableColor } from './stableColor'

type EvolutionResponse = components['schemas']['EvolutionResponse']
type PieResponse = components['schemas']['PieResponse']
type HeatmapResponse = components['schemas']['HeatmapResponse']
type CountryResponse = components['schemas']['CountryResponse']

function Frame(props: {
  label: string
  summary: string
  height: number
  /** Canvas height at the mobile breakpoint (CSS decides; defaults to `height`). */
  narrowHeight?: number
  /** Wide charts (heatmap): the canvas keeps this min width inside its own horizontal scroller, parked at the end. */
  scrollX?: { minWidth: number; scrollLabel: string; key: unknown }
  /** Controls drawn over the canvas (map zoom). */
  overlay?: ReactNode
  handleRef?: { current: ChartHandle | null }
  build: (t: ChartTheme) => Opt
  note?: ReactNode
  legend?: ReactNode
  table: ReactNode
  register?: () => void
  onClick?: (p: { componentType?: string; name?: string; data?: unknown }) => void
}) {
  const ref = useECharts(props.build, { register: props.register, onClick: props.onClick }, props.handleRef)
  const scroller = useRef<HTMLDivElement>(null)
  const scrollKey = props.scrollX?.key
  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollLeft = el.scrollWidth
  }, [scrollKey])
  const style = {
    '--chart-h': `${props.height}px`,
    '--chart-h-narrow': `${props.narrowHeight ?? props.height}px`,
    ...(props.scrollX ? { minWidth: props.scrollX.minWidth } : {}),
  } as CSSProperties
  const canvas = <div ref={ref} role="img" aria-label={`${props.label}. ${props.summary}`} className="chart-canvas" style={style} />
  return (
    <figure className="chart" style={{ margin: 0, minWidth: 0 }}>
      <div className="chart-stage">
        {props.scrollX ? (
          <div ref={scroller} className="chart-scroll" role="region" tabIndex={0} aria-label={props.scrollX.scrollLabel}>{canvas}</div>
        ) : canvas}
        {props.overlay}
      </div>
      {props.legend}
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
  const label = (i: number) => (unit === 'month' ? buckets[i].start.slice(0, 7) : buckets[i].start)
  const build = useMemo(() => (t: ChartTheme) => buildEvolutionOption(data, t), [data])
  const partial = partialIdx >= 0 ? buckets[partialIdx] : null
  return (
    <Frame
      label={`Evolution of ${dimension}, ${series.length} series per ${unit}`}
      summary={`${series.length} series over ${buckets.length} ${unit} buckets from ${data.range.from} to ${data.range.to} (${data.range.tz}). Not cumulative.`}
      height={360}
      narrowHeight={300}
      build={build}
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
  const build = useMemo(() => (t: ChartTheme) => buildPieOption(data, t), [data])
  return (
    <Frame
      label={`Share of ${dimension}`}
      summary={`${slices.length} slices${slices[0] ? `; largest: ${slices[0].label} (${pct(slices[0].share)})` : ''}.`}
      height={340}
      narrowHeight={300}
      build={build}
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

// ---------- Heatmap (rolling window, no year logic) ----------
export function HeatmapChart({ data }: { data: HeatmapResponse }) {
  const max = useMemo(() => data.cells.reduce((m, c) => Math.max(m, c.listens), 0), [data.cells])
  const total = useMemo(() => data.cells.reduce((s, c) => s + c.listens, 0), [data.cells])
  const active = useMemo(() => data.cells.filter((c) => c.listens > 0), [data.cells])
  const build = useMemo(() => (t: ChartTheme) => buildHeatmapOption(data, t), [data])
  const heatWidth = useMemo(() => buildRollingGrid(data.cells, data.range.from, data.range.to).cols * 15 + 40, [data])
  return (
    <Frame
      label="Daily listening heatmap, weeks start Monday"
      summary={`${nf.format(total)} listens across ${active.length} active days from ${data.range.from} to ${data.range.to} (${data.range.tz}); busiest day ${nf.format(max)} listens.`}
      height={7 * 16 + 28}
      scrollX={{ minWidth: heatWidth, scrollLabel: 'Daily listening heatmap, scroll sideways for earlier weeks', key: data }}
      build={build}
      legend={
        <div className="heat-legend" aria-hidden="true">
          <span>Less</span>
          {HEAT_LEVELS.map((share, i) => (
            <i key={i} style={{ background: `color-mix(in srgb, var(--accent) ${share * 100}%, var(--surface-2))` }} />
          ))}
          <span>More</span>
        </div>
      }
      table={
        <table>
          <caption className="sr-only">Listens per day</caption>
          <thead>
            <tr>
              <th scope="col">Day</th>
              <th scope="col">Listens</th>
            </tr>
          </thead>
          <tbody>
            {active.map((c) => (
              <tr key={c.day}>
                <th scope="row">{c.day}</th>
                <td>{nf.format(c.listens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    />
  )
}

// ---------- Country ----------
let mapRegistered = false
function registerWorldMap() {
  if (mapRegistered) return
  echarts.registerMap(MAP_NAME, worldGeoJson as unknown as Parameters<typeof echarts.registerMap>[1])
  mapRegistered = true
}

export type CountryChartProps = {
  data: CountryResponse
  /** ISO alpha-2 of the highlighted country; null/undefined clears the highlight. */
  selectedCountry?: string | null
  /**
   * Fired with the code when a country WITH listens is clicked (or chosen from the keyboard
   * list); fired with null when the already-selected country is clicked again (toggle off).
   * Countries without listens are not selectable.
   */
  onCountrySelect?: (code: string | null) => void
}

function MapZoom({ handleRef }: { handleRef: { current: ChartHandle | null } }) {
  return (
    <div className="map-zoom" role="group" aria-label="Map view">
      <button type="button" aria-label="Zoom in" onClick={() => handleRef.current?.zoomBy(1.5)}>+</button>
      <button type="button" aria-label="Zoom out" onClick={() => handleRef.current?.zoomBy(1 / 1.5)}>−</button>
      <button type="button" aria-label="Reset map view" onClick={() => handleRef.current?.resetView()}>⟲</button>
    </div>
  )
}

export function CountryChart({ data, selectedCountry = null, onCountrySelect }: CountryChartProps) {
  const handleRef = useRef<ChartHandle | null>(null)
  const countries = data.countries
  const max = countries.reduce((m, c) => Math.max(m, c.listens), 0)
  const sel = selectedCountry?.toUpperCase() ?? null
  const withListens = useMemo(() => new Set(countries.map((c) => c.code.toUpperCase())), [countries])
  const build = useMemo(
    () => (t: ChartTheme) => {
      registerWorldMap()
      return buildCountryOption(data, t, sel)
    },
    [data, sel],
  )
  const markerCount = countries.filter((c) => !polygonCodes.has(c.code.toUpperCase()) && markers[c.code.toUpperCase()]).length
  const select = (code: string) => onCountrySelect?.(code.toUpperCase() === sel ? null : code.toUpperCase())
  const onClick = useCallback(
    (p: { data?: unknown; name?: string }) => {
      const d = p.data as { code?: string } | undefined
      const code = (d?.code ?? p.name ?? '').toUpperCase()
      if (!code || !withListens.has(code)) return
      select(code)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [withListens, sel, onCountrySelect],
  )
  return (
    <Frame
      label="Listens by artist country"
      summary={`${countries.length} countries${markerCount ? ` (${markerCount} shown as markers because they have no polygon)` : ''}${countries[0] ? `; top: ${countryName(countries[0].code)} (${pct(countries[0].share)})` : ''}. ${data.partial_credit_note}`}
      height={380}
      narrowHeight={280}
      handleRef={handleRef}
      overlay={<MapZoom handleRef={handleRef} />}
      build={build}
      onClick={onClick}
      register={registerWorldMap}
      legend={
        <>
          <div className="map-legend" aria-hidden="true">
            <span>0</span>
            <i />
            <span>{nf.format(max)}</span>
          </div>
          {onCountrySelect ? (
            <div className="map-picker" role="group" aria-label="Select a country">
              {countries.map((c) => {
                const code = c.code.toUpperCase()
                return (
                  <button type="button" key={code} aria-pressed={code === sel} onClick={() => select(code)}>
                    {countryName(code)}
                  </button>
                )
              })}
            </div>
          ) : null}
        </>
      }
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
                  {countryName(c.code)} ({c.code.toUpperCase()})
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
