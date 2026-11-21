import { useEffect, useRef } from 'react'
import { echarts } from './echarts'
import { readTheme, type ChartTheme } from './theme'

export type Opt = Parameters<ReturnType<typeof echarts.init>['setOption']>[0]
type Chart = ReturnType<typeof echarts.init>
type ClickParams = { componentType?: string; name?: string; data?: unknown }

/** Imperative view controls for charts with a pannable geo (the country map). */
export type ChartSize = { width: number; height: number }
export type ChartHandle = { zoomBy: (factor: number) => void; resetView: () => void }

/**
 * Init chart on a div, build the option from the current CSS-token theme, re-read the theme on
 * mount/option change/resize (re-applying only when it changed), dispose on unmount.
 */
export function useECharts(
  build: (theme: ChartTheme, size: ChartSize) => Opt,
  deps: { register?: () => void; onClick?: (p: ClickParams) => void } = {},
  handleRef?: { current: ChartHandle | null },
) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<Chart | null>(null)
  const buildRef = useRef(build)
  const themeKey = useRef('')
  const clickRef = useRef(deps.onClick)
  const register = deps.register

  const apply = (keepView = true) => {
    const chart = chartRef.current
    if (!chart) return
    const theme = readTheme()
    const size = { width: chart.getWidth(), height: chart.getHeight() }
    themeKey.current = JSON.stringify([theme, size])
    const option = buildRef.current(theme, size) as { geo?: Record<string, unknown> }
    // Rebuilding (selection, theme) must not throw away the user's pan/zoom.
    const view = keepView && option.geo ? (chart.getOption?.() as { geo?: { zoom?: number; center?: number[] }[] } | undefined)?.geo?.[0] : undefined
    if (view && option.geo) option.geo = { ...option.geo, zoom: view.zoom, center: view.center }
    chart.setOption(option as Opt, true)
  }

  useEffect(() => {
    clickRef.current = deps.onClick
  })

  useEffect(() => {
    const el = ref.current
    if (!el) return
    register?.()
    const chart = echarts.init(el, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.on?.('click', (p: ClickParams) => clickRef.current?.(p))
    if (handleRef) {
      handleRef.current = {
        zoomBy: (factor) => chart.dispatchAction({ type: 'geoRoam', componentType: 'geo', zoom: factor, originX: chart.getWidth() / 2, originY: chart.getHeight() / 2 }),
        resetView: () => apply(false),
      }
    }
    let ro: ResizeObserver | undefined
    const onResize = () => {
      chart.resize()
      if (JSON.stringify([readTheme(), { width: chart.getWidth(), height: chart.getHeight() }]) !== themeKey.current) apply()
    }
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(onResize)
      ro.observe(el)
    } else {
      window.addEventListener('resize', onResize)
    }
    return () => {
      ro?.disconnect()
      window.removeEventListener('resize', onResize)
      chart.dispose()
      chartRef.current = null
      if (handleRef) handleRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [register])

  useEffect(() => {
    buildRef.current = build
    apply()
  }, [build])

  return ref
}
