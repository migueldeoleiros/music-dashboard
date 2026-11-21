import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type AnyOpt = Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any

const h = vi.hoisted(() => ({
  charts: [] as { setOption: ReturnType<typeof vi.fn>; resize: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn>; on: ReturnType<typeof vi.fn> }[],
  handlers: {} as Record<string, (p: unknown) => void>,
  registerMap: vi.fn(),
}))

vi.mock('../src/components/charts/echarts', () => ({
  echarts: {
    init: vi.fn(() => {
      const c = {
        setOption: vi.fn(),
        resize: vi.fn(),
        dispose: vi.fn(),
        on: vi.fn((ev: string, fn: (p: unknown) => void) => {
          h.handlers[ev] = fn
        }),
      }
      h.charts.push(c)
      return c
    }),
    registerMap: h.registerMap,
  },
}))

import { buildRollingGrid } from '../src/components/charts/builders'
import { CountryChart, EvolutionChart, HeatmapChart, PieChart, stableColor } from '../src/components/charts'
import { esc, mixColors, parseColor, readTheme, seriesColor } from '../src/components/charts/theme'

const roCallbacks: (() => void)[] = []
const disconnect = vi.fn()

beforeEach(() => {
  h.charts.length = 0
  roCallbacks.length = 0
  disconnect.mockClear()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(cb: () => void) {
        roCallbacks.push(cb)
      }
      observe() {}
      disconnect = disconnect
    },
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const lastOption = (): AnyOpt => h.charts[0].setOption.mock.calls.at(-1)![0]
const base = {
  coverage: {} as never,
  fixture: true,
  generation: 1,
  last_updated: null,
}
const range = { from: '2026-01-01', to: '2026-01-20', tz: 'UTC' }

describe('stableColor', () => {
  it('is deterministic per key; other is grey', () => {
    expect(stableColor('a')).toBe(stableColor('a'))
    expect(stableColor('a')).not.toBe(stableColor('b'))
    expect(stableColor('x', true)).toBe(stableColor('y', true))
  })
})

describe('EvolutionChart', () => {
  const data = {
    ...base,
    range,
    dimension: 'artists',
    bucket_unit: 'week',
    buckets: [
      { start: '2026-01-05', end: '2026-01-12', partial: false },
      { start: '2026-01-12', end: '2026-01-19', partial: false },
      { start: '2026-01-19', end: '2026-01-26', partial: true },
    ],
    series: [
      { key: 'a', label: 'A', is_other: false, values: [5, 0, 2] },
      { key: 'o', label: 'Other', is_other: true, values: [1, 1, 1] },
    ],
  } as const

  it('stacked filled smooth areas with token colors and partial markLine', () => {
    render(<EvolutionChart data={data as never} />)
    const o = lastOption()
    expect(o.series[0].data).toEqual([5, 0, 2])
    expect(o.series[0].type).toBe('line')
    for (const s of o.series) {
      expect(s.stack).toBe('total')
      expect(s.areaStyle).toBeTruthy()
      expect(s.smooth).toBe(true)
      expect(s.showSymbol).toBe(false)
      expect(s.lineStyle.width).toBeLessThanOrEqual(1.5)
    }
    expect(o.color).toEqual([readTheme().series[0], readTheme().other])
    expect(o.series[0].markLine.data).toEqual([{ xAxis: 2 }])
    expect(screen.getByText(/is incomplete/)).toBeTruthy()
    expect(screen.getByRole('img').getAttribute('aria-label')).toMatch(/Evolution of artists/)
  })

  it('tooltip lists series sorted by value, skipping zeros', () => {
    render(<EvolutionChart data={data as never} />)
    const o = lastOption()
    const html = o.tooltip.formatter([
      { dataIndex: 1, color: '#111', seriesName: 'other:o', value: 1 },
      { dataIndex: 1, color: '#222', seriesName: 'key:a', value: 0 },
      { dataIndex: 1, color: '#333', seriesName: 'key:b', value: 4 },
    ])
    expect(html.indexOf('>b<')).toBe(-1) // unknown name falls back to raw seriesName, not label
    expect(html.indexOf('key:b')).toBeLessThan(html.indexOf('Other'))
    expect(html).not.toContain('key:a')
    expect(o.tooltip.backgroundColor).toBe(readTheme().surface)
  })

  it('no markArea without partial bucket', () => {
    const d = { ...data, buckets: data.buckets.map((b) => ({ ...b, partial: false })) }
    render(<EvolutionChart data={d as never} />)
    expect(lastOption().series[0].markLine).toBeUndefined()
  })
})

describe('series identity', () => {
  it('evolution names are keys; same-label and Other stay independent', () => {
    const data = {
      ...base,
      range,
      dimension: 'artists',
      bucket_unit: 'day',
      buckets: [{ start: '2026-01-01', end: '2026-01-02', partial: false }],
      series: [
        { key: 'mbid1', label: 'Same', is_other: false, values: [1] },
        { key: 'mbid2', label: 'Same', is_other: false, values: [2] },
        { key: 'other', label: 'Other', is_other: false, values: [3] },
        { key: 'other', label: 'Other', is_other: true, values: [4] },
      ],
    }
    render(<EvolutionChart data={data as never} />)
    const o = lastOption()
    const names = o.series.map((s: AnyOpt) => s.name)
    expect(new Set(names).size).toBe(4)
    expect(o.legend.formatter(names[1])).toBe('Same')
    const html = o.tooltip.formatter([{ dataIndex: 0, marker: '', seriesName: names[0], value: 1 }])
    expect(html).toContain('Same')
    expect(html).toContain('<b style="font-variant-numeric:tabular-nums">1</b>')
  })

  it('pie data names unique for same-label slices and Other', () => {
    const data = {
      ...base,
      range,
      dimension: 'artists',
      slices: [
        { key: 'a', label: 'Same', listens: 1, share: 0.25, is_other: false },
        { key: 'b', label: 'Same', listens: 1, share: 0.25, is_other: false },
        { key: 'other', label: 'Other', listens: 1, share: 0.25, is_other: false },
        { key: 'other', label: 'Other', listens: 1, share: 0.25, is_other: true },
      ],
    }
    render(<PieChart data={data as never} />)
    const o = lastOption()
    const names = o.series[0].data.map((d: AnyOpt) => d.name)
    expect(new Set(names).size).toBe(4)
    expect(o.legend.formatter(names[0])).toBe('Same')
  })
})

describe('PieChart', () => {
  const data = {
    ...base,
    range,
    dimension: 'genres',
    slices: [
      { key: 'rock', label: 'Rock', listens: 3, share: 0.75, is_other: false },
      { key: 'other', label: 'Other', listens: 1, share: 0.25, is_other: true },
    ],
  }
  it('rounded padded segments, token colors, Other uses --series-other', () => {
    render(<PieChart data={data as never} />)
    const o = lastOption()
    const s = o.series[0]
    expect(s.itemStyle.borderRadius).toBeGreaterThan(0)
    expect(s.padAngle).toBeGreaterThan(0)
    expect(s.label.show).toBe(false)
    expect(s.emphasis.scale).toBe(true)
    expect(s.emphasis.focus).toBe('self')
    expect(s.data[0].itemStyle.color).toBe(readTheme().series[0])
    expect(s.data[1].itemStyle.color).toBe(readTheme().other)
    expect(screen.getByText('Rock')).toBeTruthy()
  })
  it('theme is read from CSS tokens at runtime', () => {
    document.documentElement.style.setProperty('--series-1', '#123456')
    document.documentElement.style.setProperty('--accent', '#abcdef')
    try {
      expect(readTheme().series[0]).toBe('#123456')
      expect(readTheme().accent).toBe('#abcdef')
      render(<PieChart data={data as never} />)
      expect(lastOption().series[0].data[0].itemStyle.color).toBe('#123456')
    } finally {
      document.documentElement.style.removeProperty('--series-1')
      document.documentElement.style.removeProperty('--accent')
    }
  })
})

describe('tooltip escaping', () => {
  const evil = '<img src=x onerror=alert(1)>"&\''
  it('escapes pie, evolution, and map labels', () => {
    expect(esc(evil)).not.toMatch(/[<>"']/)
    render(
      <PieChart
        data={{ ...base, range, dimension: 'artists', slices: [{ key: 'k', label: evil, listens: 2, share: 1, is_other: false }] } as never}
      />,
    )
    const pie = lastOption()
    const name = pie.series[0].data[0].name
    const html = pie.tooltip.formatter({ name, value: 2, color: '#fff', data: { share: 1 } })
    expect(html).not.toContain('<img')
    expect(html).toContain('&#60;img')
    cleanup()
    h.charts.length = 0
    render(
      <EvolutionChart
        data={{
          ...base,
          range,
          dimension: 'artists',
          bucket_unit: 'day',
          buckets: [{ start: '2026-01-01', end: '2026-01-02', partial: false }],
          series: [{ key: 'k', label: evil, is_other: false, values: [3] }],
        } as never}
      />,
    )
    const evo = lastOption()
    const html2 = evo.tooltip.formatter([{ dataIndex: 0, color: 'red"><b>', seriesName: evo.series[0].name, value: 3 }])
    expect(html2).not.toContain('<img')
    expect(html2).not.toContain('"><b>')
    expect(html2).toContain('&#60;img')
  })
})

describe('theme helpers', () => {
  it('parses and mixes hex, rgb and hsl colors', () => {
    expect(parseColor('#fff')).toEqual([255, 255, 255, 1])
    expect(parseColor('rgb(142 229 189 / 14%)')).toEqual([142, 229, 189, 0.14])
    expect(parseColor('hsl(0 100% 50%)')!.slice(0, 3).map(Math.round)).toEqual([255, 0, 0])
    expect(mixColors('#000000', '#ffffff', 0.5)).toBe('rgb(128 128 128)')
    expect(mixColors('nonsense', '#ffffff', 0.2)).toBe('nonsense')
  })
  it('falls back when tokens are missing', () => {
    const t = readTheme()
    expect(t.accent).toBeTruthy()
    expect(t.series).toHaveLength(10)
    expect(seriesColor(t, 10)).toBe(t.series[0])
  })
})

describe('rolling heatmap grid', () => {
  it('maps a 365-day window to weeks-as-columns, Monday first', () => {
    // 2026-11-22 (Sunday) back 364 days -> 2025-11-23 (Sunday)
    const g = buildRollingGrid([{ day: '2026-11-16', listens: 6 }], '2025-11-23', '2026-11-22')
    expect(g.points).toHaveLength(365)
    expect(g.points[0]).toEqual([0, 6, 0, '2025-11-23']) // Sunday in row 6 of the first column
    expect(g.points[1]).toEqual([1, 0, 0, '2025-11-24']) // Monday starts column 1
    expect(g.points.at(-1)).toEqual([g.cols - 1, 6, 0, '2026-11-22'])
    expect(g.cols).toBe(53)
    expect(g.points.find((p) => p[3] === '2026-11-16')).toEqual([52, 0, 6, '2026-11-16'])
    expect(g.points.every((p) => p[1] === (new Date(`${p[3]}T00:00:00Z`).getUTCDay() + 6) % 7)).toBe(true)
  })
  it('handles 366-day (leap) windows', () => {
    const g = buildRollingGrid([], '2023-12-31', '2024-12-30') // Sunday start, contains 2024-02-29
    expect(g.points).toHaveLength(366)
    expect(g.points.some((p) => p[3] === '2024-02-29')).toBe(true)
    expect(g.cols).toBe(54)
  })
  it('single-day range works', () => {
    const g = buildRollingGrid([{ day: '2026-11-22', listens: 2 }], '2026-11-22', '2026-11-22')
    expect(g.points).toEqual([[0, 6, 2, '2026-11-22']])
    expect(g.cols).toBe(1)
    expect(g.monthLabels).toEqual(['Nov'])
  })
  it('month labels: on the column holding the 1st, no crowding of the first column', () => {
    const g = buildRollingGrid([], '2026-01-14', '2026-03-05')
    expect(g.monthLabels[0]).toBe('')
    expect(g.monthLabels.filter(Boolean)).toEqual(['Feb', 'Mar'])
  })
  it('uses civil dates exactly across the Apia skipped day', () => {
    const apia = buildRollingGrid([{ day: '2011-12-30', listens: 9 }], '2011-12-29', '2011-12-31')
    expect(apia.points.map((p) => p[3])).toEqual(['2011-12-29', '2011-12-30', '2011-12-31'])
    expect(apia.points[1][2]).toBe(9)
  })
})

describe('HeatmapChart', () => {
  const data = {
    ...base,
    range: { from: '2025-11-23', to: '2026-11-22', tz: 'UTC' },
    week_starts_on: 'monday',
    cells: [
      { day: '2026-02-02', listens: 7 },
      { day: '2026-02-03', listens: 1 },
    ],
  } as const

  it('renders rounded accent-scale cells, empty color for zero days, no year picker', () => {
    render(<HeatmapChart data={data as never} />)
    const t = readTheme()
    const o = lastOption()
    expect(o.calendar).toBeUndefined()
    expect(o.visualMap).toBeUndefined()
    expect(o.yAxis.inverse).toBe(true)
    expect(o.series[0].itemStyle.borderRadius).toBeGreaterThan(0)
    const pts = o.series[0].data as { value: [number, number, number, string]; itemStyle: { color: string } }[]
    expect(pts).toHaveLength(365)
    const day = (iso: string) => pts.find((p) => p.value[3] === iso)!
    expect(day('2026-02-02').value[1]).toBe(0) // Monday
    expect(day('2026-02-02').itemStyle.color).toBe(mixColors(t.surface2, t.accent, 1))
    expect(day('2026-02-03').itemStyle.color).not.toBe(t.surface2)
    expect(day('2026-03-01').itemStyle.color).toBe(t.surface2)
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.getByText('2026-02-02')).toBeTruthy()
    const tip = o.tooltip.formatter({ value: day('2026-02-02').value }) as string
    expect(tip).toContain('Mon 2026-02-02')
    expect(tip).toContain('>7<')
  })
})

describe('CountryChart', () => {
  it('registers ISO-keyed map and passes codes', () => {
    const data = {
      ...base,
      range,
      partial_credit_note: 'note',
      countries: [{ code: 'FR', listens: 5, share: 1 }],
    }
    render(<CountryChart data={data as never} />)
    expect(h.registerMap).toHaveBeenCalled()
    const geo = h.registerMap.mock.calls[0][1]
    expect(geo.features.some((f: AnyOpt) => f.properties.code === 'FR')).toBe(true)
    const s = lastOption().series[0]
    expect(lastOption().geo.nameProperty).toBe('code')
    expect(lastOption().geo.map).toBeTruthy()
    expect(s.data[0]).toMatchObject({ name: 'FR', value: 5 })
    expect(screen.getByRole("rowheader", { name: /France/ })).toBeTruthy()
  })
})

describe('CountryChart colors and selection', () => {
  const data = {
    ...base,
    range,
    partial_credit_note: 'note',
    countries: [
      { code: 'FR', listens: 9, share: 0.9 },
      { code: 'DE', listens: 1, share: 0.1 },
    ],
  }
  it('no-data is muted, data uses accent gradient (brighter with more listens)', () => {
    render(<CountryChart data={data as never} />)
    const t = readTheme()
    const o = lastOption()
    expect(o.geo.itemStyle.areaColor).toBe(t.surface2)
    expect(o.visualMap).toBeUndefined()
    const [fr, de] = o.series[0].data
    expect(fr.itemStyle.areaColor).toBe(mixColors(t.surface2, t.accent, 1))
    expect(de.itemStyle.areaColor).not.toBe(t.surface2)
    const lum = (c: string) => parseColor(c)!.slice(0, 3).reduce((a, b) => a + b, 0)
    expect(lum(fr.itemStyle.areaColor)).toBeGreaterThan(lum(de.itemStyle.areaColor))
    expect(lum(de.itemStyle.areaColor)).toBeGreaterThan(lum(o.geo.itemStyle.areaColor))
  })
  it('highlights selected country with accent outline; null clears', () => {
    const { rerender } = render(<CountryChart data={data as never} selectedCountry="de" />)
    const t = readTheme()
    const [fr, de] = lastOption().series[0].data
    expect(de.itemStyle.borderColor).toBe(t.accent)
    expect(fr.itemStyle.borderColor).toBe(t.border)
    rerender(<CountryChart data={data as never} selectedCountry={null} />)
    expect(lastOption().series[0].data[1].itemStyle.borderColor).toBe(t.border)
  })
  it('click selects countries with listens only; clicking selected clears', () => {
    const onSel = vi.fn()
    const { rerender } = render(<CountryChart data={data as never} onCountrySelect={onSel} />)
    h.handlers.click({ name: 'FR', data: { code: 'FR' } })
    h.handlers.click({ name: 'US' })
    expect(onSel).toHaveBeenCalledTimes(1)
    expect(onSel).toHaveBeenCalledWith('FR')
    rerender(<CountryChart data={data as never} selectedCountry="FR" onCountrySelect={onSel} />)
    h.handlers.click({ name: 'FR', data: { code: 'FR' } })
    expect(onSel).toHaveBeenLastCalledWith(null)
  })
  it('keyboard list toggles selection with aria-pressed', () => {
    const onSel = vi.fn()
    render(<CountryChart data={data as never} selectedCountry="DE" onCountrySelect={onSel} />)
    expect(screen.getByRole('button', { name: 'Germany' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'France' }))
    expect(onSel).toHaveBeenCalledWith('FR')
    fireEvent.click(screen.getByRole('button', { name: 'Germany' }))
    expect(onSel).toHaveBeenLastCalledWith(null)
  })
  it('map tooltip escapes and marks no-data regions', () => {
    render(<CountryChart data={data as never} />)
    const f = lastOption().tooltip.formatter
    expect(f({ name: 'ZZ' })).toContain('no listens')
    expect(f({ name: 'FR', data: { code: 'FR', value: 9, share: 0.9 } })).toContain('France')
  })
})

describe('CountryChart markers', () => {
  it('SG without polygon becomes a marker point, polygon countries do not', () => {
    const data = {
      ...base,
      range,
      partial_credit_note: 'note',
      countries: [
        { code: 'FR', listens: 5, share: 0.5 },
        { code: 'SG', listens: 5, share: 0.5 },
      ],
    }
    render(<CountryChart data={data as never} />)
    const sc = lastOption().series[1]
    expect(sc.type).toBe('scatter')
    expect(sc.coordinateSystem).toBe('geo')
    expect(sc.data).toHaveLength(1)
    expect(sc.data[0].name).toMatch(/Singapore/)
    expect(sc.data[0].value[0]).toBeCloseTo(103.85, 0)
    expect(sc.data[0].value[1]).toBeCloseTo(1.29, 0)
    expect(sc.data[0].value[2]).toBe(5)
    expect(screen.getByText(/shown as marker on map/)).toBeTruthy()
  })
})

describe('CountryChart territory markers', () => {
  it('RE and GF (map-unit supplement) become markers', () => {
    const data = {
      ...base,
      range,
      partial_credit_note: 'note',
      countries: [
        { code: 'RE', listens: 2, share: 0.5 },
        { code: 'GF', listens: 2, share: 0.5 },
      ],
    }
    render(<CountryChart data={data as never} />)
    const sc = lastOption().series[1]
    expect(sc.data).toHaveLength(2)
    expect(sc.data[0].value[0]).toBeCloseTo(55.54, 1)
    expect(sc.data[0].value[1]).toBeCloseTo(-21.11, 1)
  })

  it('HK (admin-0 label point supplement) becomes a marker', () => {
    const data = {
      ...base,
      range,
      partial_credit_note: 'note',
      countries: [{ code: 'HK', listens: 2, share: 1 }],
    }
    render(<CountryChart data={data as never} />)
    const sc = lastOption().series[1]
    expect(sc.data).toHaveLength(1)
    expect(sc.data[0].value[0]).toBeCloseTo(114.2, 0)
    expect(sc.data[0].value[1]).toBeCloseTo(22.4, 0)
    expect(screen.getByText(/shown as marker on map/)).toBeTruthy()
  })
})

describe('lifecycle', () => {
  it('resizes on observer and disposes on unmount', () => {
    const { unmount } = render(
      <PieChart data={{ ...base, range, dimension: 'artists', slices: [] } as never} />,
    )
    roCallbacks[0]()
    expect(h.charts[0].resize).toHaveBeenCalled()
    unmount()
    expect(disconnect).toHaveBeenCalled()
    expect(h.charts[0].dispose).toHaveBeenCalled()
  })
})
