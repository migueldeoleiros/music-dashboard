import type { components } from '../../api/schema'
import { EntityImage } from '../../components/ui/EntityImage'
import { EntityList } from '../../components/ui/EntityList'
import { EntityRow } from '../../components/ui/EntityRow'
import { EmptyState } from './ChartSection'

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })

type Item = components['schemas']['RankedEntity']

// On Repeat. Listens only (no percentages). #1 is a hero card, 2..N are compact EntityRows.
// Genres have no artwork at all: no image and no initials tile, just the text.
export function RankingList({ data }: { data: components['schemas']['RankingResponse'] }) {
  if (!data.items.length) return <EmptyState />
  const name = (item: Item) => item.label || `Missing ${data.dimension === 'tracks' ? 'title' : 'metadata'}`
  const note = (item: Item) => item.metadata_state === 'missing' ? 'Missing metadata' : undefined
  const showImage = data.dimension !== 'genres'
  const [first, ...rest] = data.items
  return <EntityList label={`Top ${data.dimension}`} className="ranking-entities">
    <li className="entity-hero">
      <span className="entity-ribbon">#1</span>
      {showImage && <EntityImage src={first.image_url} label={name(first)} size="hero" decorative />}
      <span className="entity-text">
        <span className="entity-hero-name" title={name(first)}>{name(first)}</span>
        {note(first) && <small className="entity-secondary">{note(first)}</small>}
        <span className="entity-hero-count">{number.format(first.listens)} <small>listens</small></span>
      </span>
    </li>
    {rest.map((item, index) => <EntityRow key={item.key} rank={index + 2} label={name(item)} src={item.image_url} showImage={showImage} secondary={note(item)} value={number.format(item.listens)} />)}
  </EntityList>
}
