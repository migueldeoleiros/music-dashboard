import type { components } from '../../api/schema'
import { stableColor } from '../../components/charts'
import { EmptyState } from './ChartSection'

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })

// Extension point (C3): ranked rows for artists/albums/tracks/genres. Items carry image_url (null today).
export function RankingList({ data }: { data: components['schemas']['RankingResponse'] }) {
  if (!data.items.length) return <EmptyState />
  return <ol className="rankings">{data.items.map((item, index) => <li key={item.key}>
    <span className="rank-number">{String(index + 1).padStart(2, '0')}</span>
    <div className="rank-name"><span>{item.label || `Missing ${data.dimension === 'tracks' ? 'title' : 'metadata'}`}</span>{item.metadata_state === 'missing' && <small>Missing metadata</small>}<div className="rank-bar" style={{ width: `${Math.min(100, Math.max(0, item.share * 100))}%`, background: stableColor(item.key) }} /></div>
    <span className="rank-value">{number.format(item.listens)}<small>{number.format(item.share * 100)}%</small></span>
  </li>)}</ol>
}
