import type { components } from '../../api/schema'
import { CompactRow, EntityImage, EntityList } from '../../components/ui'
import { artistHref } from '../../app/navigation'
import { EmptyState } from './ChartSection'
import { ArtistLink, reliableArtistKey } from '../artist-history/ArtistLinks'
import { useArtistCredits } from '../artist-history/useArtistCredits'

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })

type Item = components['schemas']['RankedEntity']
type Dimension = components['schemas']['RankingResponse']['dimension']

// Whole row links to the primary (first credited) artist; artists link to themselves; unresolved: no link.
// Other credited names link individually (above the row link) and are shown on the secondary line.
// Credits for the whole list come from one batched request (see useArtistCredits).
type Credits = ReturnType<typeof useArtistCredits>
function linksFor(dimension: Dimension, item: Item, all: Credits, excludeArtistKey?: string) {
  const credits = dimension === 'albums' || dimension === 'tracks' ? all[item.key] ?? [] : []
  const primary = dimension === 'artists' ? { key: item.key, label: item.label } : credits[0]
  const href = primary && primary.key !== excludeArtistKey && reliableArtistKey(primary.key) ? artistHref(primary.key) : undefined
  return { href, others: credits.slice(1).filter(a => a.key !== excludeArtistKey) }
}

function RankRow({ dimension, item, rank, text, note, credits, excludeArtistKey }: { dimension: Dimension; item: Item; rank: number; text: string; note?: string; credits: Credits; excludeArtistKey?: string }) {
  const { href, others } = linksFor(dimension, item, credits, excludeArtistKey)
  const secondary = others.length ? <>{note && `${note} · `}{others.map((a, i) => <span key={i}>{i > 0 && ', '}<ArtistLink artist={a} className="row-inline-link" /></span>)}</> : note
  return <CompactRow rank={rank} label={text} href={href} src={item.image_url} showImage={dimension !== 'genres'} secondary={secondary} count={number.format(item.listens)} />
}

function Hero({ dimension, item, text, note, credits, excludeArtistKey }: { dimension: Dimension; item: Item; text: string; note?: string; credits: Credits; excludeArtistKey?: string }) {
  const { href, others } = linksFor(dimension, item, credits, excludeArtistKey)
  const secondary = others.length ? <>{note && `${note} · `}{others.map((a, i) => <span key={i}>{i > 0 && ', '}<ArtistLink artist={a} className="row-inline-link" /></span>)}</> : note
  return <li className={`entity-hero${href ? ' row--link' : ''}`}>
    {dimension !== 'genres' && <EntityImage src={item.image_url} label={text} size="hero" decorative />}
    <span className="entity-text">
      <span className="entity-hero-name" title={text}>{href ? <a className="row-link" href={href}>{text}</a> : text}</span>
      {secondary && <small className="entity-secondary">{secondary}</small>}
      <span className="entity-hero-count">{number.format(item.listens)} <small>{dimension === 'genres' ? 'genre tags' : 'listens'}</small></span>
    </span>
  </li>
}

// On Repeat. Listens only (no percentages). #1 is a hero card, 2..N are compact EntityRows.
// Genre counts are tag appearances: one per distinct genre per listen (a Rock+Jazz listen adds 1 to each).
// Genres have no artwork at all: no image and no initials tile, just the text.
export function RankingList({ data, excludeArtistKey }: { data: components['schemas']['RankingResponse']; excludeArtistKey?: string }) {
  const credits = useArtistCredits(data.dimension === 'tracks' ? 'tracks' : 'albums', data.items.map(item => item.key), data.dimension === 'albums' || data.dimension === 'tracks')
  if (!data.items.length) return <EmptyState />
  const name = (item: Item) => item.label || `Missing ${data.dimension === 'tracks' ? 'title' : 'metadata'}`
  const note = (item: Item) => item.metadata_state === 'missing' ? 'Missing metadata' : undefined
  const [first, ...rest] = data.items
  return <EntityList label={`Top ${data.dimension}`} className="ranking-entities">
    <Hero dimension={data.dimension} item={first} text={name(first)} note={note(first)} credits={credits} excludeArtistKey={excludeArtistKey} />
    {rest.map((item, index) => <RankRow key={item.key} dimension={data.dimension} item={item} rank={index + 2} text={name(item)} note={note(item)} credits={credits} excludeArtistKey={excludeArtistKey} />)}
  </EntityList>
}
