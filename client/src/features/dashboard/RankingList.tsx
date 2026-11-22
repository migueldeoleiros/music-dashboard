import type { components } from '../../api/schema'
import { EntityImage } from '../../components/ui/EntityImage'
import { EntityList } from '../../components/ui/EntityList'
import { EntityRow } from '../../components/ui/EntityRow'
import { EmptyState } from './ChartSection'
import { ArtistLink, ArtistLinks } from '../artist-history/ArtistLinks'
import { useArtistCredits } from '../artist-history/useArtistCredits'

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })

type Item = components['schemas']['RankedEntity']
type Dimension = components['schemas']['RankingResponse']['dimension']

// Artists link by their own key; albums/tracks link only when the server resolves exactly one reliable credit.
function useLinks(dimension: Dimension, item: Item) {
  const enabled = dimension === 'albums' || dimension === 'tracks'
  const credits = useArtistCredits(dimension === 'tracks' ? 'tracks' : 'albums', item.key, enabled)
  const own = dimension === 'artists' ? { key: item.key, label: item.label } : null
  const artists = enabled ? credits : []
  const single = own ?? (artists.length === 1 ? artists[0] : null)
  return { single, artists: enabled && artists.length > 1 ? artists : [] }
}

function Title({ dimension, item, text, className }: { dimension: Dimension; item: Item; text: string; className?: string }) {
  const { single } = useLinks(dimension, item)
  return single ? <ArtistLink artist={single}>{text}</ArtistLink> : <span className={className}>{text}</span>
}

function RankRow({ dimension, item, rank, text, note }: { dimension: Dimension; item: Item; rank: number; text: string; note?: string }) {
  const { single, artists } = useLinks(dimension, item)
  const secondary = artists.length ? <>{note && `${note} · `}<ArtistLinks artists={artists} /></> : note
  return <EntityRow rank={rank} label={text} labelContent={single ? <ArtistLink artist={single}>{text}</ArtistLink> : undefined} src={item.image_url} showImage={dimension !== 'genres'} secondary={secondary} value={number.format(item.listens)} />
}

// On Repeat. Listens only (no percentages). #1 is a hero card, 2..N are compact EntityRows.
// Genre counts are tag appearances: one per distinct genre per listen (a Rock+Jazz listen adds 1 to each).
// Genres have no artwork at all: no image and no initials tile, just the text.
export function RankingList({ data }: { data: components['schemas']['RankingResponse'] }) {
  if (!data.items.length) return <EmptyState />
  const name = (item: Item) => item.label || `Missing ${data.dimension === 'tracks' ? 'title' : 'metadata'}`
  const note = (item: Item) => item.metadata_state === 'missing' ? 'Missing metadata' : undefined
  const showImage = data.dimension !== 'genres'
  const [first, ...rest] = data.items
  return <EntityList label={`Top ${data.dimension}`} className="ranking-entities">
    <li className="entity-hero">
      {showImage && <EntityImage src={first.image_url} label={name(first)} size="hero" decorative />}
      <span className="entity-text">
        <span className="entity-hero-name" title={name(first)}><Title dimension={data.dimension} item={first} text={name(first)} /></span>
        {note(first) && <small className="entity-secondary">{note(first)}</small>}
        <span className="entity-hero-count">{number.format(first.listens)} <small>{data.dimension === 'genres' ? 'genre tags' : 'listens'}</small></span>
      </span>
    </li>
    {rest.map((item, index) => <RankRow key={item.key} dimension={data.dimension} item={item} rank={index + 2} text={name(item)} note={note(item)} />)}
  </EntityList>
}
