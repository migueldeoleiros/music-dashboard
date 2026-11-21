import { useEffect, useRef } from 'react'
import { echarts } from './echarts'

type Option = Parameters<ReturnType<typeof echarts.init>['setOption']>[0]

/** Init chart on a div, apply option, resize with container, dispose on unmount. */
export function useECharts(option: Option, deps: { register?: () => void } = {}) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null)
  const register = deps.register

  useEffect(() => {
    const el = ref.current
    if (!el) return
    register?.()
    const chart = echarts.init(el, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    let ro: ResizeObserver | undefined
    const onResize = () => chart.resize()
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
    chartRef.current?.setOption(option, true)
  }, [option])

  return ref
}
