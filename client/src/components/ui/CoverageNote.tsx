import type { components } from '../../api/schema'

type Dimension = keyof components['schemas']['DimensionCoverage']
const labels: Record<Dimension, string> = { artists: 'Artists', albums: 'Albums', tracks: 'Tracks', genres: 'Genres', countries: 'Countries', listens: 'Listening data' }
const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })

export function CoverageNote({ coverage, dimension, lastUpdated }: { coverage: components['schemas']['DimensionCoverage']; dimension: Dimension; lastUpdated: string | null }) {
  const value = coverage[dimension]
  const percent = value.total_listens ? number.format(100 * value.included_listens / value.total_listens) : '0'
  return <footer className="coverage-note">
    <p>{labels[dimension]} available for {percent}% of listens <span>({number.format(value.included_listens)} included / {number.format(value.total_listens)} total; {number.format(value.excluded_listens)} excluded)</span></p>
    <p>Data freshness: {lastUpdated ? <time dateTime={lastUpdated}>{new Date(lastUpdated).toLocaleString(undefined, { timeZone: 'UTC' })} UTC</time> : 'No successful import yet'}</p>
  </footer>
}
