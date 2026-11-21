import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type AnyOpt = Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any

const h = vi.hoisted(() => ({
  charts: [] as { setOption: ReturnType<typeof vi.fn>; resize: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn> }[],
  registerMap: vi.fn(),
}))

vi.mock('../src/components/charts/echarts', () => ({
  echarts: {
    init: vi.fn(() => {
      const c = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
      h.charts.push(c)
      return c
    }),
    registerMap: h.registerMap,
  },
}))

import { buildYearGrid, CountryChart, EvolutionChart, HeatmapChart, PieChart, stableColor } from '../src/components/charts'

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

  it('uses per-bucket values, stable colors, partial markLine', () => {
    render(<EvolutionChart data={data as never} />)
    const o = lastOption()
    expect(o.series[0].data).toEqual([5, 0, 2])
    expect(o.series[0].type).toBe('line')
    expect(o.color).toEqual([stableColor('a'), stableColor('o', true)])
    expect(o.series[0].markLine.data).toEqual([{ xAxis: 2 }])
    expect(screen.getByText(/is incomplete/)).toBeTruthy()
    expect(screen.getByRole('img').getAttribute('aria-label')).toMatch(/Evolution of artists/)
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
    expect(html).toContain('Same: <b>1</b>')
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
  it('renders slices with stable colors and table', () => {
    const data = {
      ...base,
      range,
      dimension: 'genres',
      slices: [
        { key: 'rock', label: 'Rock', listens: 3, share: 0.75, is_other: false },
        { key: 'other', label: 'Other', listens: 1, share: 0.25, is_other: true },
      ],
    }
    render(<PieChart data={data as never} />)
    const s = lastOption().series[0].data
    expect(s[0].itemStyle.color).toBe(stableColor('rock'))
    expect(s[1].itemStyle.color).toBe(stableColor('x', true))
    expect(screen.getByText('Rock')).toBeTruthy()
  })
})

describe('HeatmapChart', () => {
  const data = {
    ...base,
    range: { from: '0001-01-01', to: '2026-03-31', tz: 'UTC' },
    week_starts_on: 'monday',
    cells: [
      { day: '2024-05-06', listens: 4 },
      { day: '2026-02-02', listens: 7 },
      { day: '2026-02-03', listens: 1 },
    ],
  } as const

  it('Monday-first cartesian grid, latest year, daily table', () => {
    render(<HeatmapChart data={data as never} />)
    const o = lastOption()
    expect(o.calendar).toBeUndefined()
    expect(o.yAxis.data).toEqual(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
    expect(o.yAxis.inverse).toBe(true)
    expect(o.series).toHaveLength(1)
    const pts = o.series[0].data as [number, number, number, string][]
    expect(pts).toHaveLength(90)
    // 2026-02-02 is a Monday -> row 0; 02-03 Tuesday -> row 1
    expect(pts.find((p) => p[3] === '2026-02-02')).toMatchObject({ 1: 0, 2: 7 })
    expect(pts.find((p) => p[3] === '2026-02-03')).toMatchObject({ 1: 1, 2: 1 })
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('2026')
    expect(screen.getByRole('option', { name: '0001' })).toBeTruthy()
    expect(screen.getByText('2026-02-02')).toBeTruthy()
    expect(screen.queryByText('2024-05-06')).toBeNull()
  })

  it('year select switches displayed year', () => {
    render(<HeatmapChart data={data as never} />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '2024' } })
    const pts = lastOption().series[0].data as [number, number, number, string][]
    expect(pts[0][3]).toBe('2024-01-01')
    expect(pts.find((p) => p[2] === 4)![3]).toBe('2024-05-06')
    expect(screen.getByText('2024-05-06')).toBeTruthy()
    expect(screen.queryByText('2026-02-02')).toBeNull()
  })

  it('uses civil dates exactly: early years and Apia boundary', () => {
    // 0001-01-01 is a Monday (proleptic Gregorian)
    const early = buildYearGrid([{ day: '0001-01-01', listens: 3 }], 1, '0001-01-01', '0001-12-31')
    expect(early.points[0]).toEqual([0, 0, 3, '0001-01-01'])
    expect(early.points).toHaveLength(365)
    expect(early.points.at(-1)![3]).toBe('0001-12-31')
    // 2011-12-30 skipped in Pacific/Apia local time; must stay a distinct civil day
    const apia = buildYearGrid([{ day: '2011-12-30', listens: 9 }], 2011, '2011-12-29', '2011-12-31')
    expect(apia.points.map((p) => p[3])).toEqual(['2011-12-29', '2011-12-30', '2011-12-31'])
    expect(apia.points[1][2]).toBe(9)
    render(
      <HeatmapChart
        data={{ ...base, range: { from: '2011-12-29', to: '2011-12-31', tz: 'Pacific/Apia' }, week_starts_on: 'monday', cells: [{ day: '2011-12-30', listens: 9 }] } as never}
      />,
    )
    const fmt = lastOption().tooltip.formatter as (p: unknown) => string
    expect(fmt({ value: [0, 4, 9, '2011-12-30'] })).toContain('2011-12-30')
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
