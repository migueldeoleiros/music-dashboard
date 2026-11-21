import { Fragment, useId } from 'react'
import type { components } from '../../api/schema'

type Coverage = components['schemas']['DimensionCoverage']
type Dimension = keyof Coverage
const rows: { key: Dimension; label: string }[] = [
  { key: 'artists', label: 'Artists' }, { key: 'albums', label: 'Albums' }, { key: 'tracks', label: 'Tracks' },
  { key: 'genres', label: 'Genres' }, { key: 'countries', label: 'Countries' },
]
const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })
const percentOf = (value: Coverage[Dimension]) => (value.total_listens ? (100 * value.included_listens) / value.total_listens : 0)

// One compact indicator for the whole period: lowest per-dimension coverage as the badge,
// per-dimension "included of total listens" in a popover (hover/focus; always in the DOM for assistive tech).
export function CoverageNote({ coverage }: { coverage: Coverage }) {
  const id = useId()
  const lowest = Math.min(...rows.map(row => percentOf(coverage[row.key])))
  return <span className="coverage">
    <button type="button" className="coverage-badge" aria-label={`Metadata coverage, lowest ${number.format(lowest)}%`} aria-describedby={id}>
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="8" cy="8" r="6.25" /><path d="M8 7.25V11.5M8 4.75v.01" strokeLinecap="round" /></svg>
      {number.format(lowest)}%
    </button>
    <span className="coverage-pop" role="tooltip" id={id}>
      <span className="coverage-title">Listens with metadata</span>
      <dl>{rows.map(row => { const value = coverage[row.key]; return <Fragment key={row.key}><dt>{row.label}</dt><dd>{number.format(value.included_listens)} of {number.format(value.total_listens)}</dd></Fragment> })}</dl>
    </span>
  </span>
}
