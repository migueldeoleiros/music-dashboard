import { useEffect, useRef } from 'react'
import { echarts } from './echarts'
import { readTheme, type ChartTheme } from './theme'

export type Opt = Parameters<ReturnType<typeof echarts.init>['setOption']>[0]
type Chart = ReturnType<typeof echarts.init>
type ClickParams = { componentType?: string; name?: string; data?: unknown }

/**
 * Init chart on a div, build the option from the current CSS-token theme, re-read the theme on
 * mount/option change/resize (re-applying only when it changed), dispose on unmount.
 */
export function useECharts(
  build: (theme: ChartTheme) => Opt,
  deps: { register?: () => void; onClick?: (p: ClickParams) => void } = {},
) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<Chart | null>(null)
  const buildRef = useRef(build)
  const themeKey = useRef('')
  const clickRef = useRef(deps.onClick)
  const register = deps.register

  const apply = () => {
    const chart = chartRef.current
    if (!chart) return
    const theme = readTheme()
    themeKey.current = JSON.stringify(theme)
    chart.setOption(buildRef.current(theme), true)
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
    let ro: ResizeObserver | undefined
    const onResize = () => {
      chart.resize()
      if (JSON.stringify(readTheme()) !== themeKey.current) apply()
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
    }
  }, [register])

  useEffect(() => {
    buildRef.current = build
    apply()
  }, [build])

  return ref
}
